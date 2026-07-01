# PantryPilot — Pantry State
*Last updated: 2026-06-26*

---

## Overview

Pantry state is PantryPilot's model of what a household currently has at home. It is the memory that makes the planning loop intelligent — without it, the agent would suggest buying toor dal even if the household just received 2kg last week.

In V1, pantry state is **inferred**, not manually logged. Users never enter quantities or tick off items. The system estimates what's at home based on what was ordered and how fast the household typically consumes it.

---

## What Pantry State Tracks

Each household has a list of `PantryItem` records:

| Field | Type | Description |
|---|---|---|
| `item_name` | string | Generic name (e.g. "Toor Dal") |
| `category` | string | Category bucket (staples / fresh produce / dairy / packaged) |
| `brand_preference` | string | Preferred brand if known (e.g. "Tata Sampann") |
| `standard_unit` | string | Unit of measure (kg, litre, pieces) |
| `last_ordered_qty` | float | Quantity ordered in last purchase |
| `last_ordered_at` | timestamp | When it was last ordered |
| `estimated_qty_remaining` | float | Estimated quantity currently at home |
| `reorder_threshold` | float | Quantity at which we trigger a reorder suggestion |
| `avg_weekly_consumption` | float | Estimated weekly consumption rate |
| `times_ordered` | int | Total times ordered via PantryPilot (grows over time) |
| `last_user_edit` | string | Last edit action by user (removed / quantity_changed / kept) |

---

## How Pantry State Is Built

### Phase 1: Bootstrap (Onboarding)

On first run, pantry state is bootstrapped from Swiggy order history via `get_orders`.

For each item found across the last 6 months of orders:
- Calculate average order frequency (how often it appears)
- Calculate average quantity ordered per purchase
- Estimate current stock based on last order date + average consumption rate

```python
# Example bootstrap logic
days_since_last_order = (today - last_ordered_at).days
estimated_consumed = (days_since_last_order / 7) * avg_weekly_consumption
estimated_qty_remaining = max(0, last_ordered_qty - estimated_consumed)
```

This gives the planning loop a starting point on day 1 — not perfect, but good enough to generate a sensible first basket.

### Phase 2: Ongoing Updates (Post-Order)

After every order placed via PantryPilot:

1. Call `get_order_details(order_id)` to fetch the confirmed item list and quantities
2. For each item in the order:
   - Set `last_ordered_qty` = quantity in this order
   - Set `last_ordered_at` = now
   - Reset `estimated_qty_remaining` = `last_ordered_qty`
   - Consumption clock restarts from this moment

Scheduled as a background task 30 minutes after `checkout` completes (allows time for order confirmation to propagate on Swiggy's side).

### Phase 3: Passive Consumption Decay

Between orders, `estimated_qty_remaining` decays passively based on `avg_weekly_consumption`.

This decay runs at loop trigger time (just before SENSE stage), not continuously:

```python
def apply_consumption_decay(item: PantryItem) -> PantryItem:
    days_elapsed = (now - item.last_ordered_at).days
    consumed = (days_elapsed / 7) * item.avg_weekly_consumption
    item.estimated_qty_remaining = max(0, item.last_ordered_qty - consumed)
    return item
```

**Why at trigger time, not continuously:** Reduces write load. Pantry state only needs to be accurate when the planning loop runs — not in real time.

---

## Consumption Rate Learning

`avg_weekly_consumption` starts as an estimate and improves over time.

### Initial estimate (bootstrap)

Derived from order history:
```
avg_weekly_consumption = avg_qty_per_order / avg_days_between_orders * 7
```

Example: If a household orders 1kg toor dal every 18 days on average:
```
avg_weekly_consumption = 1.0 / 18 * 7 = 0.39 kg/week
```

### Refinement over time

Every time an item is suggested and the user **does not** remove it from the basket → weak signal that consumption is on track.

Every time the user **removes** an item → signal that estimated stock is higher than modelled. Decay rate adjusted down slightly.

Every time a user **manually requests** an item outside the schedule ("add milk") → signal that stock ran out faster than expected. Decay rate adjusted up slightly.

After 8+ orders, consumption rates stabilise and basket suggestions become noticeably more accurate. This is the "earned trust" foundation for the Confidence Score in V2.

---

## Reorder Threshold

`reorder_threshold` is the estimated quantity at which PantryPilot flags an item for reorder.

Default thresholds by category:

| Category | Default Threshold | Reasoning |
|---|---|---|
| Staples (dal, rice, atta, oil) | 30% of standard order qty | Buffer for 2–3 days before running out |
| Fresh produce | 0 (always reorder weekly) | Short shelf life — always included |
| Dairy & eggs | 20% of standard order qty | Fast consumption, low stockpile tolerance |
| Packaged & snacks | 20% of standard order qty | Less critical, lower reorder urgency |

Users can adjust thresholds in V1.5. In V1, defaults apply.

---

## Items Not in Pantry State

Pantry state only tracks **items the household has ordered before via Swiggy** (bootstrapped from history) or **items ordered via PantryPilot** (tracked post-order).

It does not track:
- Items bought from other stores (kirana, supermarket, other apps)
- Items gifted or brought from elsewhere
- Items consumed partially by guests

This is a known limitation in V1. We make no attempt to model external purchases. The planning loop accounts for this by being slightly conservative — if in doubt, suggest the reorder. The user can always remove it from the basket.

---

## Data Model

```sql
CREATE TABLE pantry_items (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id            UUID NOT NULL REFERENCES households(id),
    item_name               TEXT NOT NULL,
    category                TEXT NOT NULL,  -- staples | fresh_produce | dairy | packaged
    brand_preference        TEXT,
    standard_unit           TEXT NOT NULL,  -- kg | litre | pieces
    last_ordered_qty        DECIMAL(8,3),
    last_ordered_at         TIMESTAMPTZ,
    estimated_qty_remaining DECIMAL(8,3) DEFAULT 0,
    reorder_threshold       DECIMAL(8,3) NOT NULL,
    avg_weekly_consumption  DECIMAL(8,3),
    times_ordered           INTEGER DEFAULT 0,
    last_user_edit          TEXT,           -- removed | qty_changed | kept | added
    last_user_edit_at       TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_pantry_items_household 
    ON pantry_items(household_id);

CREATE INDEX idx_pantry_items_reorder 
    ON pantry_items(household_id, estimated_qty_remaining, reorder_threshold);
```

**Note on order frequency support:** The `category` field maps directly to the category buckets defined in the Order Frequency section of [v1-scope.md](../v1-scope.md). When per-category frequency is unlocked in V1.5, the planning loop queries pantry state by category bucket to determine which items are due for reorder in each cycle.

---

## Pantry State in the Planning Loop

The SENSE stage fetches pantry state and applies consumption decay:

```python
def sense(household_id: str) -> HouseholdContext:
    items = db.query(PantryItem).filter_by(household_id=household_id).all()
    items = [apply_consumption_decay(item) for item in items]
    # items with estimated_qty_remaining < reorder_threshold → flagged for reorder
    return HouseholdContext(pantry_items=items, ...)
```

The PLAN rules engine then checks each item:

```python
for item in context.pantry_items:
    if item.estimated_qty_remaining < item.reorder_threshold:
        candidate_basket.add(item, qty=item.last_ordered_qty)
```

---

## What Pantry State Does NOT Do in V1

| Feature | Version |
|---|---|
| Manual quantity logging by user | V1.5 — optional, not required |
| Real-time stock tracking (IoT / smart fridge) | Not in roadmap |
| Tracking items from other stores | Not in roadmap |
| Expiry date tracking | V2 — reduces food waste, needs SKU-level data |
| Recipe-level ingredient tracking | V3 — recipe-aware planning |

---

## Open Questions

- [ ] Should we ask the user an optional "quick check" question during onboarding — "roughly how much toor dal do you have right now?" — to improve bootstrap accuracy? Or is that too much friction?
- [ ] How do we handle a household that places a large bulk order (e.g. 5kg atta instead of 1kg)? Consumption model needs to handle outlier quantities without throwing off future estimates.
- [ ] What happens to pantry state if a household pauses PantryPilot for 3 weeks? Clock should freeze or decay to zero gracefully.
- [ ] Should users be able to view their pantry state? A simple "what does PantryPilot think you have at home" screen could build trust — V1.5 candidate.
