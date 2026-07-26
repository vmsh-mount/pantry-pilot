# PRD — Pantry Zone Grid

**Status:** Ready for implementation
**Date:** 2026-07-27
**Branch:** `feature/pantry-zone-grid`
**Mockup:** [`tasks/features/pantry-arrangement-mockup.html`](./pantry-arrangement-mockup.html) — Direction B (Zone Grid), approved

---

## Problem

Every category on the Pantry page renders as the identical white rounded list card — same row style, same gray uppercase label. "Staples" looks exactly like "Dairy" which looks exactly like "Packaged"; the only thing distinguishing them is the text label. It reads as a settings list, not a pantry anyone arranged.

---

## Goals

- Each category becomes a visually distinct **zone** — a soft-tinted block with its own color identity, not a plain white card.
- Items render as a 3-column tile grid with a radial fill gauge instead of the current thin progress bar.
- Zones stay a predictable, scannable size regardless of category size — a 15-item "Packaged" category must not dwarf a 2-item "Fresh Produce" category on the page.
- The most urgent items (depleted, then low) are always visible without any extra tap, in every zone, at any size.
- Same tap-to-edit interaction as today — stepper, Save, Mark Empty, Remove. This is a visual restructuring of the existing page, not a rebuild of the editing flow.

## Non-Goals

- No change to the pantry data model, API response shape, or the `stockPct()` / status calculation logic.
- No backend changes. `GET /v1/pantry`, `PATCH /v1/pantry/{id}`, `DELETE /v1/pantry/{id}` are unchanged.
- No re-collapse of an expanded "+N more" zone in this pass — once expanded, it stays expanded until the page reloads (matches the mockup; revisit if it proves annoying in practice).
- No per-item photography or real product images — icons are emoji, matched best-effort by keyword, falling back to a category icon. Not a computer-vision or exact-match system.
- Grid stays 3 columns on mobile; no responsive column-count changes (the app is mobile-only today).

---

## Design

### Zone anatomy

```
┌─────────────────────────────────────────┐
│ 📦 Packaged                           9  │  ← zone header: icon + label + count
├─────────────────────────────────────────┤
│  ⊙        ⊙        ⊙                    │
│ Bread   Cereal    Pasta                  │  ← tile row 1
│ 0 loaf   20 g     150 g                  │
│                                           │
│  ⊙        ⊙       +4                     │
│ Biscuits Canned   more                   │  ← tile row 2 — capped at 6 slots total
│ 1 pack   3 cans                          │
└─────────────────────────────────────────┘
   background: soft category tint
```

Each tile: a circular radial gauge (conic-gradient, same fill % as today's `stockPct()`), a small status dot (top-right corner, same 3-color status system), item name, and quantity — replacing the current row's thin horizontal bar with a more tactile "gauge cluster" look.

**Accepted tradeoff:** a radial gauge is a measurably weaker encoding than a linear bar for comparing magnitude at a glance — humans read length far more accurately than angle or area (Cleveland–McGill). Whether one item is at 20% or 30% stock is harder to judge from a 38px ring than it was from the old bar. The sort+cap already guarantees the *most urgent* items are visible without a tap, which covers the primary use case ("what needs attention"); the gauge's weaker precision mainly costs fine-grained comparison *between* two low-stock items, which is a secondary use case. This tradeoff is accepted for the tactile, zone-distinct feel the gauge buys — not an oversight.

### Category tint tokens

Five known categories + fallback, extending `CATEGORY_LABELS`:

| Category | Zone background | Label/text color | Icon |
|---|---|---|---|
| `staples` | `linear-gradient(165deg, #FBF3E3, #F3E4C8)` | `#8A6423` | 🌾 |
| `dairy` | `linear-gradient(165deg, #EFF6FB, #DCEAF5)` | `#2E5F82` | 🥛 |
| `fresh_produce` | `linear-gradient(165deg, #F0F9F1, #D8F3DC)` | `#1B4332` (existing `T.greenDark`) | 🥬 |
| `packaged` | `linear-gradient(165deg, #F0EEF5, #E2DEEE)` | `#5B4E7A` | 📦 |
| `grocery` / other | `linear-gradient(165deg, #FCEEE4, #F7DCC7)` | `#A0562A` | 🛒 |

Five distinct hue families — yellow/tan, blue, green, purple, peach/orange — chosen so adjacent zones don't collapse into "the same beige card" on a small screen. This matters most for `grocery`/other: it's the catch-all for anything Swiggy's category string doesn't cleanly map to `CATEGORY_ORDER`, so in practice it's one of the more common zones, not an edge case. An earlier draft of this table used a near-neutral gray for `grocery` (averaging to `rgb(240,240,237)`) that was only ~7–10 RGB units from `packaged`'s average (`rgb(233,230,241)`) — indistinguishable in practice. The peach tint above separates by ~28 units on the blue channel alone. Both `packaged` and `grocery` zones are now rendered side-by-side in the mockup so this is visually confirmed, not just computed.

Status colors (depleted/low/stocked dot + gauge fill) are unchanged from today's `STATUS_COLOR` map.

### Item icon — best-effort keyword match, not exact matching

`item_name` is free text (Swiggy product names or user-entered), so there's no structured field to key an icon off. Approach:

- A keyword-to-emoji lookup table (~40–60 common Indian grocery terms: rice, atta, dal, sugar, salt, oil, milk, curd, butter, paneer, egg, bread, onion, tomato, potato, banana, biscuit, cereal, pasta, chips, tea, coffee, etc.), matched case-insensitively against `item_name` using **word-boundary matching** (`\bkeyword\b`), not raw substring.
- Raw substring matching was the original spec and is explicitly wrong: `"egg"` is a substring of `"veggies"` and `"vegetable"`, so "Frozen Veggies" — an entirely ordinary pantry item — would render 🥚 instead of a vegetable icon. This isn't a rare edge case; it's the kind of collision that surfaces the first week of real use. Word-boundary matching prevents it; the keyword list should still get a quick manual scan for other collisions (e.g. any short keyword that's a prefix/suffix of a common unrelated word) before shipping.
- No match → fall back to the item's **category icon** (the same icon shown in the zone header). Every tile always has *some* icon; nothing renders blank.
- This is explicitly a pragmatic lookup, not a smart matcher — expected to miss uncommon or branded product names, and that's fine since the category-icon fallback keeps it looking intentional either way.

### Overflow — capped grid + "N more"

Within a zone, items are already sorted depleted → low → stocked → alphabetical (existing `groupItems()` sort, unchanged):

- **≤ 6 items:** all render as real tiles, no cap applied.
- **> 6 items:** the first 5 (by existing sort — so the most urgent are guaranteed visible) render as real tiles; the 6th slot becomes a **`+N more`** tile (`N` = total − 5), styled distinctly (dashed border, muted). Tapping it reveals the rest, appended into the same grid, and the tile disappears.

This mirrors the `+N more` pattern already used elsewhere in the app (Orders page item previews, onboarding's go-to-items chips) — not a new UI convention.

### Editing interaction

Tapping a tile opens the **same editor** already built in `ItemRow`'s expanded state (quantity stepper, Save, Mark Empty, Remove, save-error display) — zero logic changes. The only change is *where* it renders: since a tile is too narrow (~1/3 of card width) to host the full editor, the panel renders as a **full-width strip below that zone's tile grid** (not under the individual tile), with the selected tile visually highlighted (ring/border) while its editor is open. One zone can have at most one open editor at a time, same as today's single `expandedId` state.

---

## Component Changes

All changes are in **`app/cockpit/src/app/pantry/page.tsx`** — no other files touch this.

- `CATEGORY_LABELS` extends to a richer `CATEGORY_META` map: `{ label, icon, bg, textColor }` per category (table above).
- New `ITEM_ICON_KEYWORDS: [string, string][]` lookup + `iconFor(itemName, categoryIcon)` helper implementing the keyword-match-then-fallback described above.
- `ItemRow` (currently a full-width list row) is replaced by a `ItemTile` component: renders the gauge, status dot, name, qty — reuses `stockPct()`, `STATUS_COLOR`, `fmtQty()` unchanged.
- The editor panel currently inlined under each `ItemRow` is extracted into its own `ItemEditor` component (same JSX/handlers, no logic change), rendered once per zone below the tile grid when `expandedId` matches an item in that zone.
- Rendering: `grouped.map(([cat, catItems]) => …)` now renders a zone block (`CATEGORY_META`-tinted background) containing a `tile-grid` of `ItemTile`s (capped per the overflow rule) instead of a white list card of `ItemRow`s.
- New local per-zone state: `expandedZones: Set<string>` (or equivalent) tracking which zones have had "+N more" tapped, so the extra tiles stay rendered for that category for the rest of the page's lifetime (per Non-Goals — no re-collapse).

All existing state (`items`, `counts`, `expandedId`, `editQty`, `savingAction`, `saveError`) and handlers (`handleSave`, `handleMarkEmpty`, `handleRemove`, `toggleExpand`) are unchanged — this PRD only changes what's rendered, not how state flows.

---

## Acceptance Criteria

- Every category renders as a distinctly tinted zone — no two categories look the same.
- Item tiles show a radial gauge (not a bar) with the same fill percentage the current bar shows today.
- Every tile has an icon — either keyword-matched or the category fallback, never blank.
- A zone with ≤ 6 items shows every item with no "+N more" tile.
- A zone with > 6 items shows exactly 5 real tiles (the most urgent, per existing sort) + one "+N more" tile; tapping it reveals the rest in place.
- Tapping a tile opens the exact same editor (stepper, Save, Mark Empty, Remove, error display) as today, just anchored below the zone's grid instead of under a list row.
- No change to loading, error, or empty states (all-stocked message, "No pantry items yet", fetch error) — these stay exactly as they render today.
- `npx tsc --noEmit` clean; no new network requests introduced.

---

## Files to Change

| File | Change |
|---|---|
| `app/cockpit/src/app/pantry/page.tsx` | `CATEGORY_META` map, icon keyword lookup, `ItemTile` + `ItemEditor` components replacing `ItemRow`, zone-grid rendering with overflow cap |

No backend changes. No migration. No new API routes.
