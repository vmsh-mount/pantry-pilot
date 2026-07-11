# Quick Order — PRD

## What It Is

A lightweight Swiggy Instamart ordering surface built into PantryPilot. The user searches for items, builds a basket, and places an order immediately — no planning pipeline, no waiting for the next Flow run. Every interaction (search, add, remove, quantity tweak, brand pick, final checkout) is recorded as a signal that feeds the HouseholdModel so future Flow runs get smarter.

---

## Why It Exists

Flow covers the weekly replenishment cycle well. But households have gap orders — milk runs out mid-week, a guest arrives, something breaks and needs a replacement. Today those orders happen directly on Swiggy, outside PantryPilot, and we learn nothing from them. Quick Order captures that demand signal and closes the loop.

---

## User Flow

```
Dashboard → "Quick Order" card
  → Search screen (Swiggy Instamart search)
  → Basket (add / remove / qty / brand)
  → Checkout confirmation
  → Order placed → receipt
```

---

## Dashboard Card (4th card)

```
┌─────────────────────────────────────────┐
│  Quick Order                        🛒  │
│  Order anything from Swiggy now         │
├─────────────────────────────────────────┤
│  ● Search and order in minutes       ›  │
└─────────────────────────────────────────┘
```

Static card — no live status dot. Always shows "Search and order in minutes". Tapping opens `/quick`.

---

## Screens

### 1. Search
- Prominent search bar at top (autofocused)
- Results: item name, brand, price, image (from `search_products`)
- Each result has an "Add" button; tapping adds with qty=1
- Sticky basket chip at bottom: "X items · ₹Y → Review"
- Recent searches (last 5, stored in localStorage)

### 2. Basket
- List of added items with qty stepper and remove
- Line totals, grand total
- "Place Order" CTA
- Address shown (from `preferred_address_id`); tap to open address picker (calls `GET /v1/addresses`, returns list from Swiggy). Selection is **session-only** — it sets the delivery address for this order but does not update `preferred_address_id`. The user's default address is unchanged.

### 3. Checkout Confirmation
- Summary: items, total, delivery address, estimated time
- Single "Confirm & Order" button
- Places order via MCP `checkout` → creates `Order` + `OrderItem` records

### 4. Order Placed
- Receipt: order ID, items, total
- "Back to home" link

---

## Signal Recording

Every interaction during a Quick Order session writes an `ItemSignal` row and is tagged `source="quick_order"` (new field on `item_signals`).

| Interaction | signal_type | Feeds HouseholdModel? | What it tells us |
|---|---|---|---|
| Item added to basket | `added` | Yes | Immediate need |
| Item removed from basket | `removed` | Yes | Changed mind / already stocked |
| Qty increased above 1 | `qty_increased` | Yes | Higher consumption rate |
| Qty decreased | `qty_decreased` | Yes | Smaller need than default |
| Brand chosen (when multiple results) | `brand_changed` | Yes | Brand preference |
| Item in basket at checkout | `accepted` | Yes — strongest signal | Confirmed need |
| Order placed (all items) | persisted via `Order` + `OrderItem` | Via pantry update | Stock deduction |

**Search queries are not recorded as signals.** Searches are too noisy — users search to browse, compare prices, or check availability and often don't buy. A search that never leads to an `added` has no inferential value for the HouseholdModel and would pollute anchor detection with false interest signals. Search activity may be logged separately for product analytics (e.g. query → zero results tracking) but never written to `item_signals`.

After checkout, `update_pantry_from_order` Celery task runs identically to Flow — deducts stock, updates `estimated_qty_remaining`.

`update_model` runs post-checkout. Anchor promotion threshold: **2+ total purchases of the same item across any source** (Flow + Quick Order combined), not Quick Order alone. A user who bought milk once via Flow and once via Quick Order has the same signal strength as two Quick Order purchases. The lookup lives in `update_model`, which queries `order_items` grouped by `item_name` for the household regardless of `source`.

---

## Data Model Changes

### `item_signals` — add column
```sql
ALTER TABLE item_signals ADD COLUMN source VARCHAR DEFAULT 'flow';
```
Values: `'flow'` (existing) | `'quick_order'`

**`loop_run_id` must be nullable** — Quick Order inserts pass `loop_run_id=NULL`. The Flow migration (`f1a2b3c4d5e6`) creates this column as `UUID REFERENCES loop_runs(id)` with no `NOT NULL`, which is correct. Do not add a NOT NULL constraint here. If this column is ever tightened, Quick Order inserts will break silently.

### `orders` — add column
```sql
ALTER TABLE orders ADD COLUMN source VARCHAR DEFAULT 'flow';
```
Values: `'flow'` | `'quick_order'`

No new tables needed.

---

## Backend

### New API endpoints (`/v1/quick`)

| Method | Path | Description |
|---|---|---|
| GET | `/v1/quick/search` | Proxy to MCP `search_products`; no signal recorded |
| GET | `/v1/quick/basket` | Return server-side basket for this session |
| POST | `/v1/quick/basket/add` | Add item; records `added` signal |
| PATCH | `/v1/quick/basket/item/{id}` | Update qty/brand; records `qty_*` / `brand_changed` |
| DELETE | `/v1/quick/basket/item/{id}` | Remove item; records `removed` signal |
| POST | `/v1/quick/checkout` | Place order via MCP; records `accepted` for all items; triggers post-order tasks |

Basket is stored server-side in Redis (key: `quick_basket:{household_id}`) — TTL 24h. This avoids losing items on page refresh and enables future WhatsApp "continue your basket" nudges.

### Checkout flow (reuses existing infrastructure)
1. Acquire `routine_cart_lock:{household_id}` (same Redis lock Routines uses) — fail fast if held, surface "another order is in progress" to user
2. `clear_cart` → `update_cart` (all basket items) → `checkout` via MCP
3. Release lock
4. Create `Order(source="quick_order")` + `OrderItem` rows
5. `update_pantry_from_order.delay(order_id)`
6. `process_signals(signals, household_id, loop_run_id=None, source="quick_order")`
7. `update_model(household_id)`

---

## Frontend (`/quick`)

New page at `app/cockpit/src/app/quick/page.tsx`. Three sub-views managed by local state: `search` | `basket` | `confirmation`.

Uses existing `AppShell`. Reuses the item card pattern from the basket review page.

---

## What We Don't Do (v1)

- No AI suggestions or auto-fill — pure search-driven
- No scheduled delivery slots — immediate order only
- No WhatsApp confirmation step — in-app checkout only
- No multi-store support — Swiggy Instamart only

---

## Open Questions

1. ~~**Basket persistence across sessions**~~ — Resolved: dashboard card stays static. Abandoned baskets are not surfaced. 24h Redis TTL is sufficient; users who return will search again.
2. ~~**`loop_run_id` on signals**~~ — Resolved: refactor `process_signals` to accept `household_id` directly; `loop_run_id` becomes optional. Flow passes both; Quick Order passes only `household_id`. New signature: `process_signals(signals, household_id, loop_run_id=None, source="flow")`.
3. ~~**Pantry update timing**~~ — Resolved: immediate. `update_pantry_from_order` fires right after checkout, same as Flow.
