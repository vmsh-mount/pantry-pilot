# UI-017 — Run Visibility Dashboard

**Status:** ⏳ Pending  
**Area:** Frontend + Backend  
**Depends on:** UI-015 (basket editing done)

---

## Problem

The user has no visibility into what PantryPilot is doing or has done. There is no:
- View of scheduled/upcoming runs
- In-progress status during planning pipeline
- History of past runs (completed, failed, skipped)
- Guard rail preventing duplicate triggers

The only feedback today is a WhatsApp message after the fact.

---

## Design

Three views (tabs on a single `/runs` page + dashboard widget):

1. **Dashboard widget** — "Next run" card + 3 stat chips + 4 recent runs
2. **Run history page (`/runs`)** — full paginated list, status filter, expandable item detail
3. **In-progress state** — animated pipeline stage bar, guard rail banner on dashboard

**Status badges** — UI labels mapped to real DB states:

| Badge (UI) | DB states |
|---|---|
| `in_progress` (amber) | `pending`, `sensing`, `planning`, `optimizing`, `confirmed`, `placing` |
| `awaiting_confirmation` (purple) | `awaiting_confirmation` |
| `completed` (green) | `completed` |
| `failed` (red) | `failed` |
| `skipped` (gray) | `skipped` |

The status filter dropdown on `/runs` maps badge labels to their underlying DB state sets.

---

## Backend changes

### New endpoint: `GET /v1/runs`

```
Query params: status (optional, UI badge label), limit (default 20), offset (default 0)

Response:
{
  "success": true,
  "data": {
    "runs": [
      {
        "id": "uuid",
        "state": "completed",        ← always present; dashboard pipeline bar reads raw state
        "triggered_at": "ISO8601",
        "completed_at": "ISO8601",
        "item_count": 14,
        "total_price": 847.0,
        "failure_reason": null,
        "failure_stage": null,
        "skip_reason": null
      }
    ],
    "filtered_count": 5,             ← count matching the current status filter (or total if no filter)
    "next_run_at": "ISO8601",
    "stats": {
      "total_runs": 12,              ← lifetime run count, unaffected by filter
      "last_order_total": 847.0,
      "avg_order_total": 720.0
    }
  }
}
```

`avg_order_total` must be computed with `func.avg()` SQL aggregate over completed runs — not Python-side averaging after fetching all rows.

`last_order_total` — `total_price` of the most recent run where `state = 'completed'` ordered by `completed_at DESC LIMIT 1`. Do not use failed or skipped runs (their `total_price` is NULL).

`filtered_count` — when `status` is absent, a single `COUNT(*)` over all household runs serves as both `filtered_count` and `stats.total_runs`. No need for two separate count queries in the no-filter case.

`state` is always present in every run object. The dashboard in-progress detection reads the raw state value (`sensing` vs `planning` vs `optimizing`) to highlight the correct pipeline stage.

**Status filter translation** — the backend must map the `status` query param to DB states before querying. Never query `WHERE state = 'in_progress'` (returns nothing):

```python
STATUS_MAP = {
    "in_progress":            ["pending", "sensing", "planning", "optimizing", "confirmed", "placing"],
    "awaiting_confirmation":  ["awaiting_confirmation"],
    "completed":              ["completed"],
    "failed":                 ["failed"],
    "skipped":                ["skipped"],
}
# Usage: WHERE state IN (STATUS_MAP[status]) if status else no filter
```

### New endpoint: `GET /v1/runs/{run_id}/items`

Returns `loop_run_items` for a specific run (for expandable row detail).

```
Response:
{
  "success": true,
  "data": {
    "items": [
      {
        "item_name": "Tata Salt",
        "swiggy_product_name": "Tata Salt 1kg",
        "brand": "Tata",
        "quantity": 1.0,
        "unit": "kg",
        "total_price": 28.0,
        "added_by": "rules_engine",
        "is_substitution": false,
        "original_item_name": null
      }
    ]
  }
}
```

**Ownership check:** if `run.household_id != session household_id`, return 404 (not 403) — do not leak whether a run ID exists for another household.

### Router: `app/api/runs.py` (new file)

- `GET /v1/runs` — auth guard, STATUS_MAP filter translation, query LoopRun ordered by triggered_at DESC
- `GET /v1/runs/{run_id}/items` — auth guard, 404 on ownership mismatch

Register in `app/main.py`.

---

## Frontend changes

### New page: `app/cockpit/src/app/runs/page.tsx`

Full run history list with:
- Status filter dropdown (maps UI badge labels → real DB states via `status` param)
- Expandable rows (click to load items via `GET /v1/runs/{id}/items`)
- Failed row: shows `failure_reason` + "Retry" button
- `in_progress` rows: shows current pipeline stage label, no retry
- Pagination via "Load more" button; uses `filtered_count` to know when list is exhausted

**Retry behaviour:** clicking Retry calls `POST /basket/trigger`, which creates a brand-new `LoopRun`. The failed run row stays in the list (still shows failed badge). A new run appears at the top once triggered. The failed run is a permanent historical record.

### Dashboard updates: `app/cockpit/src/app/dashboard/page.tsx`

1. **Next run card** — `next_run_at` from `GET /v1/runs`, formatted date + "Plan now" button. Reschedule deferred.
2. **Guard rail** — if any run `state` is in `{pending, sensing, planning, optimizing, confirmed, placing}`, show amber banner and disable "Plan now"
3. **Stat chips** — `last_order_total`, `total_runs`, `avg_order_total` from `stats`
4. **Recent runs** — last 4 from `GET /v1/runs?limit=4`, "See all" → `/runs`
5. **Polling** — runs list call shares the same poll interval as the basket check. Do not add a separate polling loop for runs — keep it one combined fetch cycle.

### In-progress state

State → display label mapping for the pipeline bar and guard rail:

| DB state | Display |
|---|---|
| `pending` | "Queued…" (no stage bar) |
| `sensing` | Stage bar — Sense highlighted |
| `planning` | Stage bar — Plan highlighted |
| `optimizing` | Stage bar — Optimize highlighted |
| `confirmed` / `placing` | "Placing order…" (no stage bar) |

Stage bar: `Sense → Plan → Optimize → Confirm`, current stage highlighted, animated indeterminate progress bar below.

No cancel button — cancel is deferred (see Out of scope).

### API additions: `app/cockpit/src/lib/api.ts`

```ts
runs: {
  list: (params?: { status?: string; limit?: number; offset?: number }) => {
    const query = new URLSearchParams(
      Object.entries(params ?? {})
        .filter(([, v]) => v != null)
        .map(([k, v]) => [k, String(v)])
    );
    return request<RunsResponse>(`/runs?${query}`);
  },
  getItems: (runId: string) =>
    request<RunItemsResponse>(`/runs/${runId}/items`),
}
```

Filter out `undefined`/`null` values before constructing URLSearchParams to avoid `status=undefined` appearing in the query string.

---

## Files to touch

**Backend (new):**
- `app/pilot/app/api/runs.py`
- `app/pilot/app/main.py` — register router

**Backend (modify):**
- *(no changes to existing endpoints)*

**Frontend (new):**
- `app/cockpit/src/app/runs/page.tsx`

**Frontend (modify):**
- `app/cockpit/src/app/dashboard/page.tsx`
- `app/cockpit/src/lib/api.ts`

**Tests:**
- `app/pilot/tests/integration/test_runs_api.py`

---

## Acceptance criteria

- [ ] `GET /v1/runs` returns runs list; `state` always present on every run object
- [ ] `GET /v1/runs` `filtered_count` reflects current filter; `stats.total_runs` is lifetime count
- [ ] `GET /v1/runs?status=in_progress` returns runs in all 6 active DB states (not literal `in_progress`)
- [ ] `avg_order_total` computed via SQL `AVG()`, not Python
- [ ] `GET /v1/runs/{run_id}/items` returns items with `brand` and `is_substitution`
- [ ] `GET /v1/runs/{run_id}/items` returns 404 (not 403) on ownership mismatch
- [ ] Dashboard shows next run card with correct date
- [ ] Dashboard guard rail disables "Plan now" when any run is in the 6-state active set
- [ ] Dashboard stat chips show last order total, total runs, avg
- [ ] Dashboard recent runs (last 4) with correct status badges; no separate poll loop
- [ ] `/runs` page status filter correctly maps badge labels to DB state sets
- [ ] Expandable rows show item list with brand + substitution context
- [ ] Failed rows show failure reason + retry; retry creates new run, failed row stays
- [ ] `pending` state shows "Queued…"; `confirmed`/`placing` shows "Placing order…"; `sensing/planning/optimizing` shows stage bar
- [ ] `api.ts` list() strips undefined/null params before URLSearchParams
- [ ] Unauthenticated requests to all new endpoints return NOT_AUTHENTICATED

---

## Out of scope

- **Cancel run** — deferred. Requires adding state re-check guards to every node in `planning_graph.py` before it's safe. Without those guards, a "cancelled" run can still place an order.
- Reschedule flow
- Multiple schedule types (daily milk, weekly groceries)
- Push/WhatsApp notifications for run start
