# UI-022 — Product Flow Redesign (Quick Order + Consistency)

**Status:** 🔲 Todo  
**Area:** Frontend only  
**Depends on:** UI-021 (layout consistency — done)  
**Backend impact:** New endpoint — `GET /v1/quick/orders/recent` (see Data notes)  
**Design prototype:** [`design/ui/flows.html`](../../design/ui/flows.html)

---

## Problem

1. **Quick Order basket is hard to discover** — the search → select → basket flow uses a floating chip that users miss. The basket lives in a separate view, disconnected from search.
2. **No consistency across products** — Flow, Routines, and Quick Order each have different patterns for search, add item, edit basket, and place order.
3. **Quick Order page looks empty** — entry state is just a search box with no context or recent orders.

---

## Design reference

Open `design/ui/flows.html` in a browser. Relevant screens:

| Screen ID | What it shows |
|---|---|
| `qo-empty` | Quick Order entry state — search CTA + recent orders |
| `qo-search` | Inline search dropdown with results + OOS state |
| `qo-items` | Basket with items, qty steppers, "+ Add more", Place Order CTA |
| `qo-ordered` | Success with itemized receipt |
| `flow-basket` | Gold standard basket card (green header, categories, budget bar) |
| `rt-new-2` | Routines step 2 — same ItemSearchDropdown, items list with remove ✕ |

---

## Changes required

### Quick Order (`app/cockpit/src/app/quick/page.tsx`)

- **Remove** the floating basket chip and separate `basket` view
- **Replace** with a single unified card (same structure as Flow basket):
  - Neutral card header: "Your basket · N items"
  - Item rows with qty steppers (−/count/+) and remove ✕
  - Dashed "+ Add more items" button at bottom (opens `ItemSearchDropdown`)
  - Total footer
  - "Place Order · ₹X" primary button below the card
- **Empty state**: card with icon + description + "Search to add items" dashed button + recent quick orders list below
- **Search**: inline search dropdown (reuse `ItemSearchDropdown` from `app/cockpit/src/components/basket/`) — no separate search view

### Flow (`app/cockpit/src/app/flow/page.tsx`)

Mostly already correct. Add:
- Budget bar in green card header (% of `weekly_budget_max` used — fetch from `GET /v1/settings`)
- Edit summary banner (local `hasEdited` state — see Data notes below for condition)

Substitution badge is deferred — requires a DB schema change (`original_sku_id` on `loop_run_items`).

### Routines new/edit (`app/cockpit/src/app/routines/new/page.tsx`, `[id]/edit/page.tsx`)

- Step 2 items screen: use `ItemSearchDropdown` inline (same component)
- Items list: show each added item with qty and remove ✕ button
- Enable "Create routine" / "Save changes" only once ≥1 item is added

---

## Shared component

`ItemSearchDropdown` (`app/cockpit/src/components/basket/ItemSearchDropdown.tsx`) — also exports `BasketItemRow` from the same directory. Must work identically in all three products. No product-specific forks.

`[id]/page.tsx` (routine detail view) is **out of scope** — it shows items read-only and does not use `ItemSearchDropdown`.

---

## Data notes

### Quick Order — recent orders data source

There is no quick-order-specific orders endpoint. Use `GET /v1/quick/basket` (already exists) for the active basket; for the recent orders list in the empty state, add a new endpoint `GET /v1/quick/orders/recent` that queries the `orders` table filtered by `source = "quick_order"` and `household_id`, ordered by `created_at DESC`, limit 5. Return `order_id`, `placed_at`, `grand_total`, `item_count`. The existing `GET /v1/orders` pulls from Swiggy history and is not suitable (no `source` filter).

### Flow — edit summary banner condition

The banner is **session-local only**: track with `const [hasEdited, setHasEdited] = useState(false)`, set to `true` on any add or remove in the current session, never read from the API. This means the banner never appears on a fresh page load even if prior-session edits exist — that's intentional (the basket shown is the current state; the banner is a "you changed something" nudge, not a persistent diff indicator). Do not use `added_by` from the API response to trigger the banner.

### Flow — substitution badge

Item-level substitution is not currently tracked in the DB or API response. Defer the substitution badge to a follow-up task — it requires a schema change (`original_sku_id` on `loop_run_items`). Remove from this task's scope.

---

## Acceptance criteria

- [ ] Quick Order: no basket chip; basket appears inline in card from the moment the first item is added
- [ ] Quick Order: empty state shows description + recent quick orders (from new `GET /v1/quick/orders/recent`, limit 5)
- [ ] Quick Order: OOS items visible in search results with "OOS" label (disabled); if an OOS item is somehow already in the basket, show a warning inline and disable "Place Order"
- [ ] Flow: budget bar visible in green card header when basket is ready (`weekly_budget_max` from settings)
- [ ] Flow: edit summary banner shown (local state `hasEdited`) after the user adds or removes any item in the current session
- [ ] All three products: `ItemSearchDropdown` from `app/cockpit/src/components/basket/` used for adding items — no product-specific forks
- [ ] Routines new (`new/page.tsx`): step 2 uses `ItemSearchDropdown`; "Create routine" enabled only when item list length ≥ 1
- [ ] Routines edit (`[id]/edit/page.tsx`): step 2 uses `ItemSearchDropdown`; "Save changes" enabled whenever item list length ≥ 1 (existing items count, not just newly added)
- [ ] `[id]/page.tsx` (routine detail): no changes required — out of scope
