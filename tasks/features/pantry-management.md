# PRD — Pantry Management UI

**Status:** Ready for implementation  
**Date:** 2026-07-20  
**Branch:** `feature/pantry-management`

---

## Problem

The `pantry_items` table is the single most important input to the Flow planning pipeline — it determines which items get reordered, at what quantities, and how often. But users have no way to see or correct it from the cockpit.

This creates two failure modes:

1. **Silent drift.** Consumption decay is applied automatically before each run. Over time the estimates diverge from reality (a bag of rice was used faster than expected; milk was restocked manually). The user never sees this; Flow just plans based on stale numbers.
2. **No recovery path.** If the pipeline makes a bad call (seeds a wrong quantity, creates a duplicate item, tracks something the household no longer buys), the only fix today is `make seed` or direct SQL — neither is user-accessible.

A pantry screen closes this loop: users can verify what Flow thinks, correct what's wrong, and trust the results more.

---

## Goals

- Let users see every tracked item with its current estimated stock level
- Let users correct quantity estimates when reality diverges from the model
- Let users remove items the household no longer buys
- Surface which items are depleted or running low at a glance
- Preserve the model's data (avg_weekly_consumption, reorder_threshold) — users only touch `estimated_qty_remaining` directly

---

## Out of Scope (v1)

- Adding new pantry items manually (items enter via Flow / order history bootstrap)
- Editing `reorder_threshold` (power-user feature, future)
- Editing `avg_weekly_consumption` directly (model learns this automatically)
- Search / filter within the pantry list
- Pantry history / audit log

---

## Navigation

Pantry is added as a **5th tab** in the bottom nav, between Home and Orders:

```
🏠 Home  |  🥫 Pantry  |  📦 Orders  |  🔄 Routines  |  ⚙️ Settings
```

Rationale: Pantry is a primary-use destination, not a settings screen. Users will visit it after an order arrives (to correct estimates) or before triggering Flow (to verify the baseline). Placing it second makes it discoverable without burying it.

---

## Screen Layout

Same two-section pattern as the dashboard: full-width green hero at the top, `#F4F4F4` gray body below. No AppShell wrapper (AppShell fills the entire viewport with green — wrong for this pattern).

```
┌─────────────────────────────────────┐
│  HERO  (bg: #2D6A4F, full-bleed)   │
│   🥦 PantryPilot          ⚙        │
│                                     │
│   PANTRY                            │
│   22 items                          │
│   ─────────────────────────────     │
│   3           1                     │
│   running low    depleted           │
│                                     │
├─────────────────────────────────────┤
│  BODY  (bg: #F4F4F4)               │
│                                     │
│  STAPLES                   ← section label
│  ┌─────────────────────────────┐   │
│  │● Rice          ████░░  2.5 kg│  │
│  │● Dal           ██░░░░  0.8 kg│  ← amber dot (low)
│  │● Sugar         ░░░░░░  0 kg  │  ← red dot (depleted)
│  └─────────────────────────────┘   │
│                                     │
│  DAIRY                              │
│  ┌─────────────────────────────┐   │
│  │● Milk          ████░░  2 L  │   │
│  └─────────────────────────────┘   │
│  ...                                │
└─────────────────────────────────────┘
```

Tapping any item row expands an **inline editor** below that row (no separate page, no modal):

```
│  │● Dal           ██░░░░  0.8 kg  ∨ │
│  │                                   │
│  │  Quantity   [ − ][ 0.8 ][ + ] kg │
│  │  [ Mark Empty ]        [ Save  ]  │
│  │  [ Remove item ]                  │
│  └───────────────────────────────────┘
```

---

## Hero Section

- **Background:** `#2D6A4F`, full-bleed, no border-radius
- **Logo bar:** identical to dashboard (🥦 PantryPilot left, settings gear right)
- **Label:** "PANTRY" — 11px, 700, uppercase, `rgba(255,255,255,.45)`
- **Count:** `{N} items` — 34px, 900, white, `letter-spacing: -1px`
- **Stats footer** (same hairline-divider pattern as dashboard):
  - Only shown when `low > 0` or `depleted > 0`
  - Columns: running low count | depleted count (only show columns that are non-zero)
  - Label: 11px, `rgba(255,255,255,.4)`; Number: 18px, 800, `rgba(255,255,255,.9)`
  - If both counts are 0: show "All items stocked ✓" in 13px, `rgba(255,255,255,.7)`

---

## Body Section

### Category grouping

Items are grouped by `category`. Display order and labels:

| `category` value | Display label |
|---|---|
| `staples` | Staples |
| `dairy` | Dairy |
| `fresh_produce` | Fresh Produce |
| `packaged` | Packaged |
| `grocery` | Grocery |
| anything else | Other |

**Section label:** 11px, 700, uppercase, `letter-spacing: 0.1em`, `#8E8E93` (same as dashboard "RECENT ORDERS")

**Sort within each category:** depleted → low → stocked, then alphabetical within each status tier.

---

### Item row (collapsed)

`padding: 12px 14px`, `display: flex`, `align-items: center`, `gap: 10px`

| Part | Spec |
|---|---|
| Status dot | 8px circle. Red `#C0392B` (depleted), amber `#C87941` (low), green `#40916C` (stocked) |
| Item name | 13px, 500, `#1C1C1E`, `flex: 1`, truncated |
| Stock bar | 64px wide, 3px tall, `border-radius: 99px`. Track: `rgba(0,0,0,.07)`. Fill: see below |
| Qty text | 12px, `#8E8E93`, tabular-nums, right-aligned, `min-width: 52px`. Format: `{qty} {unit}` |
| Chevron | `∨` / `∧` toggle, 11px, `#C7C7CC` |

**Stock bar fill color:** same hue as status dot at 75% opacity.

**Stock bar fill width:**
- Primary: `min(1, estimated_qty_remaining / last_ordered_qty)` × 100%
- Fallback (no `last_ordered_qty`): `min(1, estimated_qty_remaining / (reorder_threshold × 4))` × 100%
- Depleted (qty ≤ 0): 0%

**Qty display format:**
- `qty ≤ 0` → `0 {unit}`
- `qty < 0.1` → `< 0.1 {unit}`
- `qty ≥ 10` → `{Math.round(qty)} {unit}`
- otherwise → `{Math.round(qty × 10) / 10} {unit}`

Row dividers: `0.5px solid rgba(0,0,0,.05)`

---

### Item row (expanded — inline editor)

Slides open below the collapsed row. `border-top: 0.5px solid rgba(0,0,0,.05)`, `padding: 10px 14px 14px`.

**Qty stepper row:**
- Label: "Quantity" — 11px, `#8E8E93`, `width: 64px`
- Stepper: `[ − ] [ {value} ] [ + ]` in a single rounded container
  - Container: `border: 1px solid rgba(0,0,0,.10)`, `background: #FAFAFA`, `border-radius: 10px`
  - `−` / `+` buttons: 36px × 36px tap targets, `color: #2D6A4F`, `font-size: 18px`
  - Input: `type="number"`, `text-align: center`, `font-size: 14px`, `font-weight: 600`, `color: #1C1C1E`, no border, transparent bg
  - Step is derived from `standard_unit`:  `kg` / `L` → 0.25 | `g` / `ml` → 50 | all others → 1
- Unit label: 12px, `#8E8E93`, after the stepper

**Action buttons row** (below stepper, `margin-top: 10px`):
- `[ Mark Empty ]` — left button, `flex: 1`, `border-radius: 10px`, `background: rgba(200,121,65,.10)`, `border: 1px solid rgba(200,121,65,.20)`, `color: #C87941`, 12px, 600. Sets qty to 0, saves immediately, and collapses the row on success.
- `[ Save ]` — right button, `flex: 1`, `border-radius: 10px`, `background: #2D6A4F`, `color: white`, 12px, 600. Saves `editQty`, collapses row.
- `[ Remove item ]` — full-width, below the two above, ghost style: `color: #8E8E93`, `background: rgba(0,0,0,.05)`, `border: 1px solid rgba(0,0,0,.08)`. Sets `is_active = false` (soft-delete), removes row from list.

**Saving state:** "Save" button shows "Saving…" and is disabled. Input is locked (pointer-events: none, opacity: 0.5) during the save — prevents in-flight ambiguity about which value was sent.

---

## Status Logic

Computed on the backend, returned in the API response:

| Condition | Status |
|---|---|
| `estimated_qty_remaining <= 0` | `"depleted"` |
| `estimated_qty_remaining <= reorder_threshold` | `"low"` |
| otherwise | `"stocked"` |

After a PATCH (qty update), the frontend recomputes status locally from the returned item and updates the list without a full refetch.

---

## Empty State

When `total_items = 0`:

```
┌─────────────────────────────┐
│  No pantry items yet        │ ← 15px, 600, #8E8E93
│  Items appear here after    │ ← 13px, #AEAEB2
│  your first Flow run        │
└─────────────────────────────┘
```

Centred in the body, `padding-top: 64px`.

---

## API Spec

### `GET /v1/pantry`

Returns all active pantry items for the authenticated household. Applies **no decay** — returns the stored `estimated_qty_remaining` as-is (decay runs before Flow, not on every page load).

**Response:**
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "uuid",
        "item_name": "Basmati Rice",
        "category": "staples",
        "standard_unit": "kg",
        "estimated_qty_remaining": 2.5,
        "reorder_threshold": 1.5,
        "avg_weekly_consumption": 1.0,
        "last_ordered_qty": 5.0,
        "last_ordered_at": "2026-07-14T10:00:00Z",
        "times_ordered": 8,
        "status": "stocked"
      }
    ],
    "counts": {
      "total": 22,
      "low": 3,
      "depleted": 1
    }
  }
}
```

### `PATCH /v1/pantry/{item_id}`

Updates `estimated_qty_remaining` for a single item.

**Request body:**
```json
{
  "estimated_qty_remaining": 0.5
}
```

**Response:** Updated `PantryItemOut` (same shape as one item in the list, including recomputed `status`).

**Errors:** `NOT_FOUND` if `item_id` does not belong to this household or is inactive.

### `DELETE /v1/pantry/{item_id}`

Soft-deletes the item (`is_active = false`). The item no longer appears in Flow runs or the pantry list.

**Response:**
```json
{ "success": true, "data": { "deleted": true } }
```

---

## State & Data Flow

```
mount → GET /v1/pantry → render grouped list
                         ↓
         tap row → expand inline editor (local state)
                         ↓
         tap Save / Mark Empty → PATCH /v1/pantry/{id}
                         ↓
         on success → update item in local list (no refetch)
                      recompute counts
                      collapse editor
                         ↓
         tap Remove → DELETE /v1/pantry/{id}
                         ↓
         on success → splice item from local list
                      recompute counts
```

No optimistic updates — wait for API response before updating UI. Latency is low (local network) and a failed save showing stale data is worse than a brief loading state.

---

## Schema Note

The five fields in the API response shape (`category`, `standard_unit`, `last_ordered_qty`, `last_ordered_at`, `times_ordered`) already exist on the `pantry_items` ORM model (`app/pilot/app/models/db.py` lines 198–209). CLAUDE.md lists only a subset of the model's columns — it is not exhaustive. **No migration is required.**

---

## Files to Change

| File | Change |
|---|---|
| `app/pilot/app/schemas/pantry.py` | NEW — `PantryItemOut`, `PantryItemUpdate` |
| `app/pilot/app/api/pantry.py` | NEW — `GET`, `PATCH`, `DELETE` endpoints |
| `app/pilot/app/main.py` | Register pantry router |
| `app/cockpit/src/app/pantry/page.tsx` | NEW — pantry screen |
| `app/cockpit/src/lib/api.ts` | Add `api.pantry.*` namespace |
| `app/cockpit/src/components/ui.tsx` | Add Pantry tab to `NAV_TABS` |
