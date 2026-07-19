# PRD — Nutrition Tracking: All Order Types

**Status:** Draft  
**Date:** 2026-07-19  
**Scope:** One backend change (add `order_id` to runs API) + frontend across three screens

---

## Problem

NutritionCard is currently only shown on the Quick Order confirmed screen. Flow and Routines place orders that go through the same nutrition resolution pipeline but have no UI surface to show the result. Users who primarily use Flow or Routines — the majority — never see their nutrition data.

---

## Goal

Show nutrition data for every order, regardless of how it was placed, with zero extra effort when a new order type is introduced in future.

---

## Non-goals

- No changes to NutritionCard's display or macro rendering logic (visual output stays as-is)
- No new nutrition API endpoints (existing `GET /v1/nutrition/order/{order_id}` works for all order types)
- No nutrition data on the basket review / confirmation step (pre-order — no `order_id` exists yet)
- No weekly aggregate nutrition UI (separate task)

---

## Prerequisite: fix NutritionCard infinite poll

**File:** `app/cockpit/src/components/nutrition/NutritionCard.tsx`, line 96

**Bug:** When nutrition resolution permanently fails (worker down, all items unresolvable), the poll callback reschedules itself indefinitely — `setTimeout(poll, 10_000)` with no retry cap. The component leaks timers and keeps firing API calls even after the user navigates away.

**Fix:** Add a retry counter ref. After 5 failed computing responses, set state to `"error"` and stop polling:

```tsx
const retryRef = useRef(0)

// inside poll(), before scheduling the next timeout:
if (retryRef.current >= 5) { setState("error"); return }
retryRef.current += 1
pollRef.current = setTimeout(poll, 10_000)
```

This must land before any history screen ships, because history screens open NutritionCard for orders that may never resolve — without the cap they poll forever.

---

## Where NutritionCard appears

| Screen | File | Current | Target |
|---|---|---|---|
| Quick Order — confirmed view | `app/quick/page.tsx` | ✅ shown | no change |
| Flow — runs list | `app/runs/page.tsx` | ❌ | ✅ shown when a run is expanded, if `order_id` present |
| Routines — run history | `app/routines/[id]/page.tsx` | ❌ | ✅ shown when a run row is expanded |
| Orders history | `app/orders/page.tsx` | ❌ | ✅ shown when an order card is expanded |

---

## Screen-by-screen design

### 1. Flow — Runs page (`app/cockpit/src/app/runs/page.tsx`)

**Endpoint:** `GET /v1/runs` (router at `app/pilot/app/api/runs.py`)

**Problem:** `RunSummary` (TypeScript interface, `lib/api.ts:27`) does **not** include `order_id`. The field exists on the DB model (`LoopRun.order_id`) and is joined in the runs query, but is not serialised into the API response. This must be added before the frontend can render NutritionCard for Flow runs.

**Backend change required:**
1. Add `order_id: str | None` to the serialised run dict in `runs.py`
2. Add `order_id: string | null` to `RunSummary` in `lib/api.ts`

**UI change:** Each completed run row gets an expand toggle. Expanded state shows NutritionCard below the item list. Runs without `order_id` (skipped, failed) render no toggle. **Trigger:** a "🌿 Nutrition" text button in the run row footer (right-aligned, same style as existing secondary actions); clicking it toggles the card open/closed.

---

### 2. Routines — Run history (`app/cockpit/src/app/routines/[id]/page.tsx`)

**`order_id` status:** Already present — `RoutineRun` interface at line 9 has `order_id?: string`. No backend change needed.

**UI change:** Add an expand toggle to each `placed` run row. Expanded view shows NutritionCard. Only render the toggle when `run.status === "placed"` and `run.order_id` is non-null. One run expanded at a time — opening a new one closes the previous. **Trigger:** a downward chevron icon (▾) on the right side of the run row; clicking anywhere on the row toggles expand.

---

### 3. Orders history (`app/cockpit/src/app/orders/page.tsx`)

**`order_id` status:** Present on every card — `order_id: string` in the orders API response. No backend change needed.

**UI change:** Add an expand toggle to every order card. Expanded view shows NutritionCard. This becomes the canonical place to review nutrition for any past order regardless of type. **Trigger:** a "🌿 Nutrition" text button in the card footer (left-aligned alongside the existing item preview chips); clicking toggles the card open/closed. Clicking the row body outside the button does nothing — orders page cards are not otherwise interactive.

---

## NutritionCard behaviour in history context

On the confirmed screen, nutrition is almost always still computing when first shown, so the loading spinner is the expected initial state. In history context, the order may be hours or days old:

- Resolution is likely already complete → card loads immediately on first poll
- If resolution failed (worker was down at order time), the card shows spinner then "unavailable" after 5 retries — acceptable, retry cap prevents infinite polling

No behaviour changes to NutritionCard needed beyond the infinite-poll fix above.

---

## Implementation order

1. **Fix infinite poll in `NutritionCard.tsx`** — prerequisite for all history screens (see details above)
2. **Orders page** (`app/orders/page.tsx`) — highest value, covers all order types, `order_id` always present, no backend change
3. **Routines run history** (`app/routines/[id]/page.tsx`) — `order_id` already in interface, no backend change
4. **Flow runs page** (`app/runs/page.tsx`) — requires backend change first: add `order_id` to `GET /v1/runs` serialisation and `RunSummary` interface
