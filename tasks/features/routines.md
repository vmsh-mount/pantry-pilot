# UI-022 + BE-006 — Routines

**Status:** ⏳ Pending  
**Area:** Frontend + Backend  
**Depends on:** None (independent of existing loop run pipeline)

---

## Problem

The current PantryPilot flow generates a basket automatically and asks the user to confirm before placing. This works for unpredictable weekly shopping, but it fails for items the user buys on a fixed, predictable cadence — milk every day, vegetables every Sunday, cleaning supplies every month. For these items, the user has no intent to review or edit. They just want the order to happen.

**Routines** is a standing-order system: the user defines a fixed set of items and a schedule, and PantryPilot places the order automatically on each occurrence without asking for confirmation.

---

## Goals

- User can create a routine: name, items with quantities, frequency, schedule time, duration
- Multiple routines can run in parallel, independently
- Each routine run places a Swiggy order automatically — no confirmation step
- User can pause, resume, skip a single run, edit, or delete a routine
- User receives a WhatsApp notification after each order is placed (not before)
- Dashboard shows active routines at a glance

## Non-goals (MVP)

- AI item suggestions during routine creation (manual item entry only)
- Conflict detection between routines and weekly basket
- Quantity auto-adjustment based on consumption patterns
- WhatsApp-based routine creation or management
- Sub-day frequency (every few hours)
- Conditional routines ("only if I'm home")
- In-app notifications — WhatsApp only for MVP (see gap 6)

---

## Key design decisions

### 1. Auto-place, notify after

Routines place orders silently. No confirmation window before each run. This is the core value: set it and forget it. The user can skip an upcoming run from the app if they know they won't need it.

Rationale: if we require confirmation, routines become indistinguishable from the existing basket review flow. The whole point is removing that friction.

**Edge case — some items out of stock:** Skip unavailable items, place the rest. Notify user which items were skipped. Status = `partial`.

**Edge case — all items out of stock:** Abort the run. Status = `failed`. Notify user.

### 2. Item resolution strategy

At routine creation, the user types item names. The frontend calls `search_products` (debounced) as the user types, shows results, and the user confirms the exact SKU. The resolved `swiggy_product_id` and `swiggy_product_name` are stored on `routine_items` at creation time.

At run time, the stored SKU is used directly. If it returns unavailable, the system runs a fresh `search_products` using the item name + household brand preferences, updates the stored SKU if a match is found, or marks the item as skipped for this run.

This avoids re-running SKU resolution on every order while gracefully handling delisted products.

### 3. Quantities

User specifies quantity per run for each item (e.g., 2 packets of milk). Quantities are fixed — the system does not adjust based on pantry state. The user chose this frequency and quantity deliberately.

### 4. Pause behaviour

Pausing a routine suspends future runs. `paused_at` is recorded. On resume, `total_days_paused` is incremented by `(now - paused_at).days`. If `end_date` is set (non-ongoing), it is extended by `total_days_paused` days. `next_run_at` is recomputed from the frequency starting from today and written back to the routines row.

For ongoing routines (`end_date = null`), pausing simply stops runs. No end date to extend.

### 5. Duration / end condition

Three options at creation:
- **Fixed duration**: 2 weeks / 1 month from start date → computes `end_date`
- **Pick end date**: calendar picker → sets `end_date` directly
- **Ongoing**: `end_date = null`, runs until user pauses or deletes

On the routine detail screen, "runs remaining" is shown for fixed-duration routines and "Ongoing" for routines where `end_date = null`.

### 6. Skip a run

`POST /v1/routines/{id}/skip-next` marks the current `next_run_at` as skipped (creates a `routine_run` row with `status=skipped`, `skip_reason="user_skip"`) and advances `next_run_at` to the following occurrence. Does not affect end date.

### 7. Frequency model

`frequency_type` enum: `every_n_days`, `weekly`, `monthly`

`fortnightly` is dropped as a separate enum value — it is represented as `every_n_days` with `frequency_value = 14`. The UI offers "Every 2 weeks" as a preset that sets this under the hood.

| frequency_type | frequency_value meaning |
|---|---|
| `every_n_days` | N (1 = daily, 2 = every 2 days, 14 = fortnightly, etc.) |
| `weekly` | Day of week: 0 = Monday … 6 = Sunday (matches Python `date.weekday()`) |
| `monthly` | Day of month: 1–28. Always calendar month (not fixed 30 days). Day 29–31 not allowed to avoid Feb/month-end ambiguity |

**weekly day mapping:** Uses Python `date.weekday()` throughout — 0 = Mon, 6 = Sun. The Beat task's next-run computation uses the same convention. Explicitly noted here to prevent off-by-one bugs.

**monthly:** A monthly routine set for day 10 fires on the 10th of each calendar month, not 30 days after the last run. This matches user mental model ("order on the 10th").

### 8. next_run_at as source of truth

`next_run_at` is stored on the `routines` table and is the authoritative signal for when the next run should fire. It is updated in three places:

- **After each run completes** (placed, partial, or failed): advance by the frequency interval
- **After skip-next**: advance by the frequency interval
- **After resume**: recompute from today using the frequency interval

The Beat task (`check_due_routines`) queries: `SELECT * FROM routines WHERE status = 'active' AND next_run_at <= now() + interval '15 minutes'`. No join to `routine_runs` needed.

`next_run_at` is set at routine creation to `start_date + schedule_time`.

### 9. Cart collision prevention

When two routines for the same household are due at the same time, their `execute_routine_run` Celery tasks may run in parallel and corrupt each other's Swiggy cart.

**Solution:** Before any Swiggy cart operation, acquire a Redis distributed lock keyed by `household_id` with a TTL of 5 minutes. If the lock cannot be acquired within 30 seconds, abort the run and log `status=failed`, `skip_reason="lock_timeout"`. This serialises all Swiggy cart interactions per household.

```python
lock_key = f"routine_cart_lock:{household_id}"
with redis_client.lock(lock_key, timeout=300, blocking_timeout=30):
    # clear cart, add items, checkout
```

### 10. Token expiry at run time

There is no silent background token refresh for Swiggy OAuth. The `reauth` flow requires user interaction. If the token is expired or invalid when a routine task fires, the task aborts immediately: creates a `routine_run` with `status=failed`, `skip_reason="token_expired"`, and sends a WhatsApp notification: "Your [routine name] order couldn't be placed — your Swiggy session expired. Open the app to reconnect."

The existing `check_token_expiry` Beat task (runs daily at 9 AM IST) proactively warns users before expiry. Routines depend on this task running successfully to prevent surprise failures.

### 11. PATCH race condition

`PATCH /v1/routines/{id}` edits take effect on the next scheduled run. Any `execute_routine_run` task in progress loads its data at task start and is not affected by concurrent edits. This is acceptable because tasks complete in seconds to minutes. Document this in the response: `{"message": "Changes will take effect from the next run"}`.

### 12. In-app notifications

Deferred. WhatsApp only for MVP. When `whatsapp_enabled = false`, send no notification (log the gap). In-app notification persistence requires a new table or Redis queue — not worth the complexity for MVP. Revisit when in-app notification infrastructure exists.

---

## Files to touch

| File | Change |
|---|---|
| `app/pilot/app/models/db.py` | Add `Routine`, `RoutineItem`, `RoutineRun` ORM models; add `routine_run_status` and `skip_reason` enums |
| `app/pilot/migrations/` | New Alembic migration: create `routines`, `routine_items`, `routine_runs` tables |
| `app/pilot/app/api/routines.py` | New FastAPI router: all `/v1/routines/*` endpoints |
| `app/pilot/app/api/products.py` | New FastAPI router: `GET /v1/products/search?q=` |
| `app/pilot/app/main.py` | Register `routines` and `products` routers |
| `app/pilot/app/schemas/routines.py` | Pydantic request/response models for routines API |
| `app/pilot/app/services/routines_service.py` | Domain logic: create, pause, resume, skip, edit, delete, `compute_next_run_at()` |
| `app/pilot/app/tasks/routines.py` | `execute_routine_run` Celery task; `check_due_routines` Beat task |
| `app/pilot/app/config.py` | Add Beat schedule entry for `check_due_routines` (every 15 minutes) |
| `app/cockpit/src/app/routines/page.tsx` | Routines list screen |
| `app/cockpit/src/app/routines/new/page.tsx` | Create routine — 3-step flow |
| `app/cockpit/src/app/routines/[id]/page.tsx` | Routine detail screen |
| `app/cockpit/src/app/routines/[id]/edit/page.tsx` | Edit routine — pre-populated 3-step form |
| `app/cockpit/src/app/dashboard/page.tsx` | Add routines summary section |
| `app/cockpit/src/components/ui.tsx` | Add bottom nav "Routines" tab |
| `app/cockpit/src/lib/api.ts` | Add `api.routines.*` and `api.products.search()` client methods |

---

## Data model (new tables)

### `routines`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| household_id | UUID FK | |
| name | text | user-defined label |
| status | enum | `active`, `paused`, `ended`, `deleted` |
| frequency_type | enum | `every_n_days`, `weekly`, `monthly` |
| frequency_value | int | N for `every_n_days`; 0–6 (Mon–Sun, `date.weekday()`) for `weekly`; 1–28 for `monthly` |
| schedule_time | time | HH:MM **stored as UTC**. Frontend collects local IST time from user; API converts to UTC before persisting (IST = UTC+5:30, so 8:00am IST → 02:30 UTC). `next_run_at` is computed using the UTC `schedule_time` — Beat fires at the UTC time, never at the IST wall-clock value accidentally. |
| start_date | date | |
| end_date | date | nullable — null means ongoing |
| next_run_at | timestamp | source of truth for next execution; updated after every run, skip, or resume |
| paused_at | timestamp | nullable |
| total_days_paused | int | default 0; accumulated for end-date extension |
| created_at | timestamp | |

### `routine_items`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| routine_id | UUID FK → routines.id ON DELETE RESTRICT | |
| item_name | text | user-facing name (e.g. "Milk 1L") |
| quantity | float | per-run quantity |
| unit | text | "unit", "kg", "L", etc. |
| swiggy_product_id | text | resolved SKU, nullable until confirmed at creation |
| swiggy_product_name | text | display name from Swiggy search result |

### `routine_runs`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| routine_id | UUID FK → routines.id ON DELETE RESTRICT | NOT CASCADE — past runs preserved when routine is soft-deleted |
| scheduled_at | timestamp | when this run was due |
| status | enum | `placed`, `partial`, `failed`, `skipped` |
| order_id | UUID FK | nullable; links to `orders` table |
| skipped_items | jsonb | list of `{item_name, reason}` for unavailable items |
| skip_reason | text | nullable — `"user_skip"`, `"all_items_unavailable"`, `"token_expired"`, `"lock_timeout"`, `"missed"`, `"household_paused"` |
| placed_at | timestamp | nullable |
| total_amount | numeric | nullable |

**Note on FK:** Both `routine_items` and `routine_runs` use `ON DELETE RESTRICT`. Soft deleting a routine (setting `status=deleted`) preserves all child rows. A hard delete must not be possible via the API — only status transitions.

---

## Backend changes

### New Celery task: `execute_routine_run`

Queue: `planning` (reuse existing queue).

**Task logic:**

1. Load routine + `routine_items` + household
2. Check `household.is_paused`. If true: create `routine_run(status=skipped, skip_reason="household_paused")`, advance `next_run_at`, return. No notification sent.
3. Acquire Redis cart lock for `household_id` (timeout 300s, blocking 30s). If lock fails: create `routine_run(status=failed, skip_reason="lock_timeout")`, return
4. Check Swiggy token validity. If expired: create `routine_run(status=failed, skip_reason="token_expired")`, notify via WhatsApp, return
5. `clear_cart()`
6. For each item:
   a. Try stored `swiggy_product_id`
   b. If unavailable: run `search_products(item_name, brand_prefs)`, update `routine_items.swiggy_product_id` if found, else mark item skipped
7. If all items skipped: create `routine_run(status=failed, skip_reason="all_items_unavailable")`, notify, advance `next_run_at`, return
8. `update_cart()` with available items
9. `checkout()`
10. Create `order` + `order_items` records
11. Create `routine_run(status="placed" or "partial")` with `order_id`, `skipped_items` if any
12. Send WhatsApp notification (placed / partial / failed message — see Notifications)
13. Advance `next_run_at`: compute next occurrence from current `next_run_at + frequency_interval`. If new `next_run_at > end_date` (and `end_date` is not null), set `routine.status = "ended"`
14. Release Redis lock

### New Celery Beat task: `check_due_routines`

Fires every 15 minutes. Two separate queries — missed runs first, then due runs.

```python
now = datetime.utcnow()

# 1. Missed runs: next_run_at is in the past by >15min (Beat was down or fell behind)
#    Log as skipped and advance next_run_at. Never retry.
missed = db.query(Routine).filter(
    Routine.status == "active",
    Routine.next_run_at < now - timedelta(minutes=15),
).all()
for r in missed:
    db.add(RoutineRun(
        routine_id=r.id,
        scheduled_at=r.next_run_at,
        status="skipped",
        skip_reason="missed",
    ))
    r.next_run_at = compute_next_run_at(r)  # advance past the missed slot
db.commit()

# 2. Due runs: next_run_at is within the next 15-minute window
due = db.query(Routine).filter(
    Routine.status == "active",
    Routine.next_run_at >= now - timedelta(minutes=15),  # don't re-enqueue already-fired
    Routine.next_run_at <= now + timedelta(minutes=15),
).all()
for r in due:
    execute_routine_run.apply_async(args=[r.id], eta=r.next_run_at)
```

Processing missed runs first ensures `next_run_at` is advanced before the due-run query, so a routine that was missed and is now also "due" in the current window is only logged as missed — not enqueued again.

The `eta` parameter on `apply_async` ensures the task fires at the exact `next_run_at` time, not immediately. The lower bound `next_run_at >= now - 15min` in the due query prevents double-enqueuing when Beat restarts within the same window.

### New API routes (`/v1/routines`)

| Method | Path | Description |
|---|---|---|
| GET | `/v1/routines` | List all routines for household |
| POST | `/v1/routines` | Create routine |
| GET | `/v1/routines/{id}` | Routine detail + upcoming run schedule |
| PATCH | `/v1/routines/{id}` | Edit routine; takes effect on next run |
| DELETE | `/v1/routines/{id}` | Soft delete (`status=deleted`); returns 200 |
| POST | `/v1/routines/{id}/pause` | Pause; records `paused_at` |
| POST | `/v1/routines/{id}/resume` | Resume; extends end_date, recomputes `next_run_at` |
| POST | `/v1/routines/{id}/skip-next` | Skip next run; advances `next_run_at` |
| GET | `/v1/routines/{id}/runs` | Paginated list of past `routine_runs` |
| GET | `/v1/products/search?q=` | Shared product search (wraps `search_products` MCP) |

**`GET /v1/products/search?q=`** is a new shared endpoint under its own router (`/v1/products`), not under `/v1/basket` or `/v1/routines`. The existing `GET /v1/basket/search?q=` has an `awaiting_confirmation` guard that would reject calls made outside an active basket session — it must not be reused here. The new endpoint has no basket-state guard: it requires only an active session cookie (household authenticated).

**`GET /v1/routines/{id}` response includes:**
- Routine fields
- `runs_remaining`: integer if `end_date` is set, `null` if ongoing (frontend renders "Ongoing"). Frontend must not compute this client-side.
- `upcoming_runs`: list of next 5 computed run timestamps. Computation rules:
  - `every_n_days`: `next_run_at + N * timedelta(days=1)` repeated
  - `weekly`: next occurrence of `frequency_value` weekday at `schedule_time`, then +7 days each
  - `monthly`: use `dateutil.relativedelta(months=1)` — not `timedelta(days=30)`. Example: routine on day 10, `next_run_at = Jan 10` → upcoming = [Feb 10, Mar 10, Apr 10, …]
- `total_runs`: integer for fixed-duration routines (`ceil` of calendar-aware count), `null` for ongoing. Frontend renders "Ongoing" when null. Do not compute client-side — call the API.

---

## Frontend changes

### Bottom nav

Add "Routines" tab (Tabler icon: `ti-repeat`) between Home and Orders. 4 tabs total.

### Dashboard

Add "Routines" section below the basket card:
- Up to 2 active routines shown (name, items summary, next run time)
- "View all" → `/routines`
- Empty state: "Set up a routine to automate recurring orders" with "+ New routine" link

### Screens

**`/routines` — Routines list**
- Active routines first, then paused, then ended/deleted
- Each row: emoji icon, name, frequency label, next run time, status chip
- Empty state with "Create your first routine" CTA

**`/routines/new` — Create (3 steps)**

*Step 1 — Name + items*
- Text input: routine name
- Search input: calls `GET /v1/products/search?q=` (new shared endpoint), debounced 400ms
- Selected items: removable tags, each with an inline quantity input (default 1)
- Quick-add chips: pulled from `pantry_seeds` stored during onboarding inference — no new MCP call

*Step 2 — Schedule*
- Frequency: vertical checkmark list (one selected at a time) — "Every day / Every 2 days / Every 3 days / Weekly / Every 2 weeks / Monthly / Custom (every N days)". Selected row has green border + green text + checkmark. Not a segmented control.
  - Weekly → day-of-week picker (Mon–Sun) revealed below the selected row
  - Monthly → day-of-month picker (1–28) revealed below the selected row
- Schedule time: labelled "Schedule time" (not "Delivery time"). Preset button grid — 7am / 8am / 9am / 10am / 12pm / 6pm. Same selected styling as frequency (green border + text).
- Duration: button row — "2 weeks / 1 month / Pick end date / Ongoing"

*Step 3 — Review*
- Summary card: name, items, frequency, schedule time, start date, end date
- Total runs: computed and shown for fixed-duration routines; "Ongoing" for open-ended
- Upcoming runs: first 3 dates
- "Start routine" CTA → `POST /v1/routines`

**`/routines/[id]` — Detail**
- Header: emoji, name, status chip, runs remaining (integer or "Ongoing")
- Summary card: all routine fields (tap "Edit" → `/routines/[id]/edit`)
- Upcoming runs list: next 5 runs, each with inline "Skip" link
- Past runs: collapsible section, links to placed orders
- Footer CTA: Pause (active) / Resume (paused) + Edit routine button
- Delete: top-right destructive action with confirmation dialog

**Paused state (detail screen variant):**
- Status chip changes to amber "Paused"
- Upcoming runs section replaced with a yellow info banner: "Routine paused — no orders will be placed until you resume. End date extended to [new date]."
- Footer: single "Resume routine" primary button (amber fill)

**Edit screen (`/routines/[id]/edit`):** Same 3-step form, pre-populated. On save, shows toast: "Changes saved — takes effect from the next run."

---

## Notifications (WhatsApp)

**Placed:**
> Your [name] order was placed — [item 1], [item 2] · ₹[total]. On its way!

**Partial (some items skipped):**
> Your [name] order was placed — [available items] · ₹[total]. Not available this time: [skipped items].

**Failed — all out of stock:**
> Your [name] order couldn't be placed — all items were out of stock this time. Check the app to skip or adjust.

**Failed — token expired:**
> Your [name] order couldn't be placed — your Swiggy session expired. Open the app to reconnect.

When `whatsapp_enabled = false`: no notification sent for MVP. Log the gap in structured logs.

---

## Edge cases

| Scenario | Behaviour |
|---|---|
| Two routines due simultaneously | Redis cart lock serialises execution; second task waits up to 30s |
| Lock timeout (>30s wait) | `routine_run(status=failed, skip_reason="lock_timeout")`; `next_run_at` still advanced |
| Token expired at run time | Abort; `skip_reason="token_expired"`; notify user; `next_run_at` advanced |
| Household paused (`is_paused=true`) | `skip_reason="household_paused"`; advance `next_run_at`; do not extend end date; no notification |
| Beat was down; run missed by >15m | `skip_reason="missed"`; advance `next_run_at`; never retry |
| User edits routine mid-run | In-progress task uses data loaded at task start; edit takes effect on next run |
| Routine deleted mid-run | In-progress run completes; no further runs scheduled |
| Monthly routine, end of month | Day-of-month 1–28 only; no 29–31 allowed at creation to avoid ambiguity |
| `next_run_at` passes `end_date` after a run | Set `routine.status = "ended"` in the same transaction as advancing `next_run_at` |
| Ongoing routine paused then resumed past a would-be end | No end date → no extension needed; next run computed from today |

---

## Acceptance criteria

**Routine lifecycle**
- [ ] User can create a routine with at least 1 item, a frequency, a schedule time, and a duration (fixed or ongoing)
- [ ] `next_run_at` is set at creation and updated correctly after every run, skip, and resume
- [ ] Routine moves to `status=ended` automatically when `next_run_at` would exceed `end_date`
- [ ] Soft delete sets `status=deleted`; `routine_items` and `routine_runs` rows are preserved (RESTRICT FK)

**Execution**
- [ ] Routine runs fire within 15 minutes of `next_run_at`
- [ ] Orders are placed without user confirmation
- [ ] Redis cart lock prevents concurrent Swiggy cart access per household
- [ ] All items unavailable → `status=failed`, user notified, `next_run_at` advanced
- [ ] Some items unavailable → `status=partial`, order placed for available items, user notified with skipped list
- [ ] Token expired at run time → `status=failed`, user notified to re-authenticate, `next_run_at` advanced
- [ ] Missed run (Beat was down) → logged as `skip_reason="missed"`, not retried
- [ ] Household paused → logged as `skip_reason="household_paused"` (distinct from `"missed"`), no notification sent

**Pause / resume / skip**
- [ ] Pausing records `paused_at`; no runs fire while paused
- [ ] Resuming extends `end_date` by days paused (fixed-duration only); recomputes `next_run_at`
- [ ] Skipping next run creates a `routine_run(status=skipped, skip_reason="user_skip")` and advances `next_run_at`

**Frontend**
- [ ] Routines list shows active, paused, and ended routines with correct status chips
- [ ] Detail screen shows "Ongoing" (not a number) for open-ended routines
- [ ] Quick-add chips on create step 1 come from `pantry_seeds` (no extra MCP call)
- [ ] Review step shows correct total run count for fixed-duration; "Ongoing" for open-ended
- [ ] PATCH edit shows toast "Changes saved — takes effect from the next run"
- [ ] Dashboard shows up to 2 active routines with next run time

**Beat task**
- [ ] `check_due_routines` processes missed runs before due runs in the same tick
- [ ] Missed routines (`next_run_at < now - 15min`) are logged as `skip_reason="missed"` and `next_run_at` advanced — never enqueued
- [ ] Due-run query lower bound (`next_run_at >= now - 15min`) prevents double-enqueue on Beat restart within the same window
- [ ] `upcoming_runs` for `monthly` routines uses `relativedelta(months=1)`, not `timedelta(days=30)`

**Search**
- [ ] `GET /v1/products/search?q=` is a new endpoint with no basket-state guard
- [ ] Routine creation step 1 does not call `GET /v1/basket/search`

**Data integrity**
- [ ] `schedule_time` stored as UTC; frontend sends IST, API converts before persisting
- [ ] `next_run_at` computed using UTC `schedule_time` — Beat fires at correct wall-clock time
- [ ] `frequency_value` for `weekly` uses `date.weekday()` convention (0=Mon, 6=Sun)
- [ ] Monthly routines reject day-of-month values 29–31 at API validation
- [ ] No TypeScript errors; no Alembic migration errors

**Known limitations (MVP)**
- Redis cart lock TTL is 300s. If Swiggy checkout hangs beyond 5 minutes, the lock releases and the next routine for this household may run concurrently. Acceptable for MVP; revisit if checkout hangs become frequent.
