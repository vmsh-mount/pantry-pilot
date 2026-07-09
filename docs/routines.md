# PantryPilot — Routines
*Last updated: 2026-07-09*

---

## What is Routines?

Routines is a recurring-order feature that lets households automate the purchase of specific items on a fixed schedule — independent of the weekly AI-planned basket.

Where the planning loop looks at your pantry, reasons about what's running low, and builds a basket intelligently, **Routines is intentional and predictable**: you define exactly what to buy, how often, and at what time. The system places the order for you without asking.

**Examples:**
- 6L Bisleri every 3 days at 8 AM
- 1kg Amul butter every Monday at 7 AM
- 5kg Aashirvaad atta on the 1st of every month at 9 AM

---

## Why Routines is a Product

The weekly planning loop solves a real problem — households don't know exactly what to buy, so they need AI help. But there's a second class of purchase that households handle every week without thinking: **items they always buy in the same quantity on a predictable schedule**.

Milk. Water. Baby formula. Pet food. Protein powder. These don't need planning. They need automation.

Routines captures that segment. It is a product offering distinct from the planning loop:

| Dimension | Planning Loop | Routines |
|---|---|---|
| Who decides what to buy | AI (with user confirmation) | User (defined once) |
| Schedule | Household's weekly order day | User-defined per routine |
| Flexibility | Basket changes every week | Same items, same quantities |
| Confirmation step | Required (WhatsApp or UI) | None — placed automatically |
| Best for | Full grocery run | Specific recurring items |

Both can coexist: a household can have a weekly AI-planned basket *and* a daily water delivery routine.

---

## How It Works Today

### Lifecycle

```
User creates routine
        ↓
System computes first next_run_at (IST → UTC)
        ↓
Beat task fires every 15 minutes
        ↓
  ┌──────────────────────────────┐
  │  Routine due within ±15 min? │
  │  → enqueue execute task      │
  │                              │
  │  Routine missed (>15 min     │
  │  past next_run_at)?          │
  │  → log as missed, advance    │
  └──────────────────────────────┘
        ↓
execute_routine_run (Celery task)
  1. Check routine is still active
  2. Check household not paused
  3. Acquire Redis cart lock (prevents concurrent orders)
  4. Validate Swiggy token
  5. Resolve items (stored SKU → search fallback)
  6. Clear cart → push items → checkout
  7. Record RoutineRun (placed / partial / failed / skipped)
  8. Advance next_run_at
  9. Send WhatsApp notification
```

### Frequencies

| Type | Example | `frequency_value` |
|---|---|---|
| Every N days | Every 3 days | `3` |
| Weekly | Every Monday | `0` (Mon=0 … Sun=6) |
| Monthly | On the 5th | `5` (1–28) |

Schedules are entered in IST and stored in UTC.

### Run Outcomes

| Status | Meaning |
|---|---|
| `placed` | All items ordered successfully |
| `partial` | Some items ordered; others unavailable or unresolvable |
| `failed` | Order not placed (token expired, all items unavailable, checkout error, lock timeout) |
| `skipped` | Household paused, or user explicitly skipped, or Beat missed the window |

### Item Resolution

Each routine item stores a `swiggy_product_id` (SKU). At execution time:
1. If the stored SKU exists → use it directly
2. If not → search Swiggy by item name, take the first result, persist the new SKU for next time
3. If nothing found → mark item as skipped

This means routines survive SKU rotation — a product going out of stock and returning under a new ID is handled automatically.

### Controls

- **Pause / Resume** — Stops routine execution. Resumes with next_run_at recomputed from now. If the routine has an end_date, it is extended by the number of paused days.
- **Skip next** — Logs a skipped run and advances to the following occurrence.
- **Delete** — Soft-delete. History preserved.
- **Duration** — Ongoing (no end) or bounded by an end date. Routines with an end date transition to `ended` automatically after the last run.

---

## API Surface

```
GET    /v1/routines                 List all routines for the household
POST   /v1/routines                 Create a routine
GET    /v1/routines/{id}            Get a single routine (with upcoming runs preview)
PATCH  /v1/routines/{id}            Update name, schedule, items, or end date
DELETE /v1/routines/{id}            Soft-delete

POST   /v1/routines/{id}/pause      Pause an active routine
POST   /v1/routines/{id}/resume     Resume a paused routine
POST   /v1/routines/{id}/skip-next  Skip the next occurrence

GET    /v1/routines/{id}/runs       Run history (status, amount, skipped items)
```

Each routine response includes:
- `upcoming_runs` — next 5 scheduled timestamps
- `runs_remaining` — for bounded routines
- `total_runs` — total occurrences over the full duration
- `schedule_time_ist` — display time in IST (stored as UTC internally)

---

## Current Limitations

- **No mid-run editing** — If a routine is in-flight (executing), a concurrent edit may conflict. This is handled via a Redis cart lock per household, but item changes take effect on the next run.
- **No delivery slot selection** — Checkout uses the household's preferred slot. Per-routine slot selection is not yet supported.
- **No budget guard** — Routines do not check the household's weekly budget. A routine can place an order regardless of what the planning loop spent.
- **No retry on partial failure** — If checkout fails, the run is logged as `failed` and the next run is scheduled normally. There is no same-day retry.
- **Monthly day capped at 28** — To avoid month-end edge cases (Feb 29, Apr 31 etc.). Day 29/30/31 not supported.

---

## Roadmap

### Near-term (V1.1)

**Smart notifications before execution**
Send a WhatsApp message 30–60 minutes before a routine runs, listing what will be ordered and the estimated total. Allow a one-tap skip from the message. This gives users control without requiring a confirmation step by default.

**Per-routine delivery slot**
Allow a routine to specify its own delivery slot (morning / afternoon / evening), independent of the household preference. Useful for water deliveries that must arrive before 8 AM.

**Budget awareness**
Check the household's weekly budget before placing. If the routine order would exceed the remaining budget for the week, send a notification and skip — or place a reduced order.

**Retry on partial failure**
If checkout fails for a transient reason (Swiggy timeout, cart error), retry once after 15 minutes before marking the run as failed.

---

### Medium-term (V1.2)

**Routine templates**
Pre-built routine suggestions based on household type and order history. "You order 6L Bisleri every week — want to automate this?" One-tap setup.

**Quantity auto-adjustment**
Track how much of each routine item is left (via pantry) and adjust the quantity for the next run. If you still have half a bottle of oil, skip or halve the order.

**Shared routines (household members)**
Allow multiple users in a household to contribute to and manage routines. Currently all routines belong to a single household account.

**Pause by duration**
"Pause for 2 weeks while I'm travelling" — auto-resume after a specified duration.

---

### Long-term (V2)

**Routine marketplace**
Community-sourced routine templates. A new household can browse "Popular routines in Bengaluru" or "Common routines for families with kids" and install one in two taps.

**Dynamic quantity from consumption**
Use the pantry decay model (already built in the planning loop) to automatically compute how much of each item to order. The routine becomes intelligent — it knows you go through 1.5kg of atta per week, so it adjusts for how long since the last run.

**Cross-platform order consolidation**
If a planning loop basket and a routine are scheduled for the same day, merge them into a single Swiggy order to avoid separate delivery fees.

**Subscription negotiation**
For items with a recurring Swiggy subscription discount, automatically prefer the subscription variant when placing routine orders.

---

## Data Model

```
routines
  id                 UUID PK
  household_id       FK → households.id
  name               "Daily Water"
  status             active | paused | ended | deleted
  frequency_type     every_n_days | weekly | monthly
  frequency_value    integer (N days / weekday 0-6 / day-of-month 1-28)
  schedule_time      TIME (stored UTC)
  start_date         TIMESTAMPTZ
  end_date           TIMESTAMPTZ nullable
  next_run_at        TIMESTAMPTZ nullable
  paused_at          TIMESTAMPTZ nullable
  total_days_paused  integer default 0

routine_items
  id                   UUID PK
  routine_id           FK → routines.id
  item_name            "Bisleri Water 2L"
  quantity             DECIMAL
  unit                 "bottle" | "kg" | "L" etc.
  swiggy_product_id    nullable (resolved at execution)
  swiggy_product_name  nullable

routine_runs
  id            UUID PK
  routine_id    FK → routines.id
  scheduled_at  TIMESTAMPTZ
  placed_at     TIMESTAMPTZ nullable
  status        placed | partial | failed | skipped
  skip_reason   nullable text
  order_id      FK → orders.id nullable
  total_amount  DECIMAL nullable
  skipped_items JSON nullable (list of {item_name, reason})
```

---

## Business Framing

Routines is the first feature in PantryPilot that can stand alone as a product.

The planning loop requires AI, pantry state, and user confirmation — it is sophisticated but also fragile and requires ongoing trust-building with the user. Routines requires none of that. A user who doesn't want AI planning their groceries can still use Routines to automate their predictable purchases.

**This unlocks two go-to-market angles:**

1. **Expand the existing user base** — Every PantryPilot user who has stable recurring purchases (water, milk, eggs) can activate a routine on day one, getting immediate value before they've built enough pantry history for the planning loop to work well.

2. **Standalone offering** — Routines can be offered as a lighter product to households who are skeptical of AI grocery planning but want simple order automation. Lower barrier to adoption, faster time-to-value.

As the routines feature matures (templates, smart notifications, quantity adjustment), it becomes a retention layer: even if a household pauses the planning loop, routines keep running.
