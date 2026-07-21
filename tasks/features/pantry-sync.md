# PRD — Pantry Sync from Order History

**Status:** Ready for implementation  
**Date:** 2026-07-21  
**Branch:** `feature/pantry-sync`

---

## Problem

`PantryService.post_order_update()` already updates pantry stock correctly after a PantryPilot-placed order. But when a user places an order directly on Swiggy (without using PantryPilot), the pantry is never told. Stock estimates drift until the next Flow run applies decay — by which point the "low" signal may fire incorrectly because the pantry thinks the item was never restocked.

The fix: after every order — PantryPilot or external — the pantry should update.

---

## Goals

- Sync external Swiggy orders (not placed by PantryPilot) into `pantry_items` automatically
- Reuse the existing `PantryService.post_order_update()` — the data model and learning loop are already correct
- Avoid re-processing PantryPilot-placed orders (already handled by `update_pantry_post_order` task)
- Avoid re-processing orders already synced in a previous run
- Run on a schedule without user action

---

## Out of Scope

- Real-time webhook from Swiggy when an external order is placed (no such webhook exists)
- Syncing orders older than 30 days on first run (cap to avoid unbounded latency)
- Showing external order history in the cockpit UI (existing `/orders` page is PP-only)

---

## Design

### Deduplication strategy

`orders.swiggy_order_id` is a unique column that stores the Swiggy order ID for every PP-placed order. When syncing external orders, fetch the Swiggy order history, then skip any order whose Swiggy order ID already exists in our `orders` table — those were placed by PP and already processed.

No new table or Redis key needed.

### Sync window

`household_preferences` gets a new column: `last_external_sync_at TIMESTAMPTZ`. On each sync run:
- If `last_external_sync_at` is set: fetch orders placed after that timestamp
- If null (first sync): fetch orders placed in the last 30 days

After a successful sync, write `last_external_sync_at = now`.

### Beat schedule

New entry in the Beat scheduler: `sync_external_orders_all` every **4 hours**. Fans out `sync_external_orders.delay(str(hh_id))` for every active, onboarded household.

4 hours is the right cadence — short enough that a morning Swiggy order is reflected before the evening Flow run, long enough that we're not hammering the Swiggy MCP.

---

## New Celery task: `sync_external_orders`

**File:** `app/tasks/pantry.py` (add alongside `update_pantry_post_order`)

```
Queue:      pantry
Max retries: 2
Retry delay: 300s
```

**Algorithm:**

```
1. Load household → get access_token, preferred_address_id
2. Load last_external_sync_at from household_preferences
3. Determine since_ts:
     if last_external_sync_at: use it
     else: now - 30 days
4. Call Swiggy MCP get_orders(limit=20), then filter client-side:
     keep only summaries where order.placed_at >= since_ts
     orders where placed_at is None (MCPOrderSummary.placed_at comes from
     o.get("createdAt") which can be omitted) should be included, not skipped —
     better to over-process than silently miss a real order
     (get_orders has no since parameter — filtering must happen after fetch;
      20 items is sufficient for 4-hour windows; first-sync 30-day lookback
      may miss heavy users but that's an acceptable one-time gap)
5. Build skip-set: load swiggy_order_ids from orders table WHERE
     household_id = hh_id AND placed_at >= since_ts - interval '1 day'
   (bounded to recent PP orders; the 1-day buffer handles clock skew)
6. For each Swiggy order NOT in the skip set:
     a. Call get_order_details(swiggy_order_id) to get line items
     b. Map items to [{name, quantity, unit, category}]
     c. Call PantryService(db).post_order_update(household_id, order_items)
7. Set household_preferences.last_external_sync_at = now
8. Commit
```

**Log events:**
- `external_sync_start` — household_id, since_ts
- `external_sync_skipped_pp_order` — swiggy_order_id (per skipped order)
- `external_sync_processed` — swiggy_order_id, item_count (per processed order)
- `external_sync_complete` — household_id, orders_processed, orders_skipped

---

## New Beat task: `sync_external_orders_all`

**File:** `app/tasks/pantry.py` (alongside `sync_external_orders` — Beat path must match module)

Fan-out task. Loads all `household_id` values where `onboarding_complete = true` and `is_active = true`, then dispatches `sync_external_orders.delay(str(hh_id))` for each.

Beat schedule entry (in `celery_app.py` or wherever Beat config lives):

```python
"sync-external-orders": {
    "task":     "app.tasks.pantry.sync_external_orders_all",
    "schedule": crontab(minute=0, hour="*/4"),
}
```

---

## Migration

One new column on `household_preferences`:

```sql
ALTER TABLE household_preferences
  ADD COLUMN last_external_sync_at TIMESTAMPTZ;
```

**File:** `app/pilot/migrations/XXXX_pantry_sync_timestamp.py`

No default — NULL means "never synced", which the task interprets as "sync last 30 days."

---

## Impact on `post_order_update` (existing task)

No changes needed. PP-placed orders are still processed immediately after checkout via `update_pantry_post_order.delay()`. The new sync task only handles external orders and skips PP orders by Swiggy order ID. The two paths feed the same `PantryService.post_order_update()` — no duplication.

---

## Impact on pantry accuracy

After this ships:
- `last_ordered_qty` is populated from real order data for all orders, not just PP orders
- The stock bar primary formula (`estimated_qty_remaining / last_ordered_qty`) works reliably
- `avg_weekly_consumption` learns from the actual purchase cadence across both order sources
- `reorder_threshold` is computed from real order quantities
- The "running low" signal is accurate within 4 hours of any Swiggy order

---

## Files to Change

| File | Change |
|---|---|
| `app/pilot/app/models/db.py` | Add `last_external_sync_at` to `HouseholdPreferences` |
| `app/pilot/migrations/XXXX_pantry_sync_timestamp.py` | NEW — add column |
| `app/pilot/app/tasks/pantry.py` | Add `sync_external_orders` and `sync_external_orders_all` tasks |
| `app/pilot/app/tasks/celery_app.py` | Add Beat schedule entry for `sync_external_orders_all` |

No frontend changes. No changes to `PantryService`, `api/pantry.py`, or the planning graph.
