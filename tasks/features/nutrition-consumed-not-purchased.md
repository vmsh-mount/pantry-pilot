# PRD — Fix Nutrition Scaling: Purchased Quantity ≠ Consumed Quantity

**Status:** Ready for implementation
**Date:** 2026-07-29
**Related:** [`nutrition-tracking.md`](nutrition-tracking.md), [`nutrition-gap-to-cart-phase-b3-gap-detection.md`](nutrition-gap-to-cart-phase-b3-gap-detection.md) (downstream consumer of the numbers this fixes)

---

## Problem

`compute_item_totals()` scales an item's nutrition by the **entire purchased pack size**, not by how much of it a household actually gets through in a week. A 5kg bag of rice at ~350 kcal/100g adds ~17,500 kcal to that order's nutrition snapshot — even though the household eats a fraction of it that week and the rest sits in the pantry for weeks to come.

This is silent. Nothing in the pipeline flags it, no confidence tier is lowered, and it's currently the single biggest distortion in the numbers — larger than any OFF/USDA/LLM confidence gap the product already discloses.

## Root Cause

`app/services/nutrition_resolution.py:670`, `compute_item_totals()`:

```python
q = resolved["quantity_g"] / 100.0   # quantity_g = full pack size, parsed from the SKU's own quantity string
...
"calories": _s("calories_per_100g"),  # scaled by q — the whole pack, every time
```

`quantity_g` comes from `_parse_quantity_g()` (`nutrition_resolution.py:101`), which parses the **SKU's pack size** ("5 kg" → 5000g) — there is no separate notion of "amount consumed" anywhere in the resolution pipeline. `resolve_order_nutrition` (`app/tasks/nutrition.py:96`) then sums this straight into `OrderNutrition.total_calories` / `nutrient_totals`.

## Blast Radius

`order_nutrition` is the base fact every nutrition feature reads — nothing downstream re-derives it independently:

- **Weekly digest** — sums `order_nutrition` across the trailing week against per-member targets.
- **Dashboard chip** ("on track" / "needs attention") — inherits whatever the digest says.
- **Gap-to-Cart** (`app/services/nutrition_gaps.py:58`, `_fetch_week_item_breakdown`) — reads `item_breakdown` from the exact same `OrderNutrition` rows to compute the actual-vs-target diff and the coverage guard. A bulk staple order can swing calorie/carb totals enough to mask a genuine protein or iron shortfall underneath the noise, or falsely show the household "over target."

This is not a display bug confined to one card — it's upstream of every number the product surfaces, including the ones the Swiggy proposal doc cites as a differentiator ("confidence disclosed per item, never smoothed over"). Pack-vs-consumed isn't a confidence problem today — it isn't modeled at all.

## Existing Building Block We Reuse

The codebase already has a learned weekly-consumption model — built for reorder thresholds, not nutrition, but exactly the right shape:

- `PantryItem.avg_weekly_consumption` (`app/models/db.py:210`) — a rolling estimate maintained by `PantryService`, refined via EMA on every reorder (`app/services/pantry_service.py:303-313`), with a `qty / 2` ("assume it lasts ~2 weeks") bootstrap default for a brand-new item (`pantry_service.py:289`).
- **Join key**: `PantryItem.item_name` is populated from `OrderItem.product_name` (`app/tasks/pantry.py:71`, `PantryService.post_order_update`), so it's an **exact string match at SKU/pack granularity** — "India Gate Basmati Rice 5kg" and "India Gate Basmati Rice 1kg" are tracked as distinct pantry rows, same as they're distinct order-item rows. No generic-name normalization to worry about.
- `avg_weekly_consumption` is stored in the item's own unit (`PantryItem.standard_unit`), not grams — same convention `quantity_g` parsing already handles via `_parse_quantity_g`.

No new consumption model, no new schema. We're wiring an existing, already-learned number into a place that currently ignores it.

## Goals

1. Attribute an order item's nutrition contribution based on **estimated consumption this week**, capped by the pack size — not the full pack, every time.
2. Reuse `avg_weekly_consumption` as the estimate; don't invent a second heuristic.
3. Gracefully fall back to today's behavior (full pack) when there's no learned rate yet — never crash, never under-attribute to zero.
4. Applies to **new orders only**, going forward from deploy.

## Out of Scope (this task)

- **Recomputing or backfilling existing `order_nutrition` rows.** Explicitly deferred — historical rows keep their current (distorted) numbers. A backfill is a separate, later task once this logic has run in production for a while.
- Any UI change to surface "partial pack consumed" vs. "carried over" — candidate Phase 2 follow-up, not required for the correctness fix itself.
- Any change to Celery task ordering/orchestration between `update_pantry_post_order` and `resolve_order_nutrition` — see [Design Decision 2](#2-no-celery-ordering-dependency-needed) for why this isn't necessary.
- New `pantry_items` columns or a migration — `avg_weekly_consumption` already exists; `item_breakdown` is JSONB, so the new fields below need no schema change.

## Files to Change

- `app/services/nutrition_resolution.py` — `estimate_consumed_g()`, `compute_item_totals()` signature.
- `app/tasks/nutrition.py` — `resolve_order_nutrition` wiring (batch `PantryItem` fetch, per-item call, `item_breakdown` fields).
- `app/cockpit/src/lib/api.ts` — `NutritionItemBreakdown` TS interface (see [Design §7](#7-frontend-contract-update-apits)). Easy to miss since nothing renders it yet — called out explicitly so it isn't rediscovered later.
- New test files per [Testing Plan](#testing-plan).

---

## Design

### 1. New helper: `estimate_consumed_g()`

Location: `app/services/nutrition_resolution.py`, alongside `compute_item_totals`.

```python
def estimate_consumed_g(
    quantity_g: float,
    avg_weekly_consumption: float | None,
    consumption_unit: str | None,
    item_name: str,
) -> float:
    """
    Cap this order's nutrition attribution at what the household is
    estimated to get through in a week, not the full purchased pack —
    a 5kg rice bag isn't eaten in the week it's bought.

    Falls back to the full pack when there's no learned rate yet (a
    brand-new item) or the rate's unit can't be parsed to grams.
    """
    if not avg_weekly_consumption or not consumption_unit:
        return quantity_g
    weekly_g = _parse_quantity_g(f"{avg_weekly_consumption} {consumption_unit}", item_name)
    if weekly_g is None:
        return quantity_g
    return min(quantity_g, weekly_g)
```

Reuses `_parse_quantity_g` — same regexes, same liquid-density table, same discrete-unit weight table already used for pack-size parsing — so a "1.2 kg/week" rate and a "5 kg" pack are interpreted with identical unit semantics. One conversion path, not two that can drift apart.

### 2. `compute_item_totals()` gains a `consumed_g` parameter

```python
def compute_item_totals(resolved: dict, consumed_g: float | None = None) -> dict:
    if resolved.get("quantity_unresolvable") or not resolved.get("quantity_g"):
        return {...}  # unchanged

    pack_g = resolved["quantity_g"]
    effective_g = pack_g if consumed_g is None else min(pack_g, consumed_g)
    q = effective_g / 100.0
    ...
```

`consumed_g=None` keeps today's full-pack behavior for any caller that doesn't (yet) supply an estimate — but the one production caller (`resolve_order_nutrition`) will always pass a value, so this is a safety default, not a silent escape hatch left in the main path.

### 3. Wiring in `resolve_order_nutrition` (`app/tasks/nutrition.py`)

Before the per-item loop, batch-fetch matching `PantryItem` rows in one query (avoid N+1):

```python
pantry_rows = await db.execute(
    select(PantryItem).where(
        PantryItem.household_id == order.household_id,
        PantryItem.item_name.in_([i.product_name for i in items]),
    )
)
pantry_by_name = {p.item_name: p for p in pantry_rows.scalars().all()}
```

Not filtering `is_active` here matches `post_order_update`'s own lookup (`pantry_service.py:270-274`) — consistent with existing behavior, not a new inconsistency. It does mean a soft-deleted pantry item's stale `avg_weekly_consumption` still gets used to cap this estimate if the household reorders that exact item name after it was removed from pantry tracking — a pre-existing quirk (soft-deleted items silently keep updating in the background) this task inherits rather than introduces. Not worth blocking on.

Inside the loop, after `resolve_item()`:

```python
p = pantry_by_name.get(item.product_name)
consumed_g = estimate_consumed_g(
    quantity_g              = resolved.get("quantity_g") or 0,
    avg_weekly_consumption  = float(p.avg_weekly_consumption) if p and p.avg_weekly_consumption else None,
    consumption_unit        = p.standard_unit if p else None,
    item_name                = item.product_name,
)
scaled = compute_item_totals(resolved, consumed_g)
```

### 4. `item_breakdown` records what happened, not just the result

Add two fields per entry (JSONB — no migration):

- `pack_quantity_g` — the full purchased pack size (today's `quantity_g`, renamed for clarity now that a second quantity exists).
- `consumed_g` — the capped value actually used for scaling.

This makes the fix debuggable/inspectable immediately, and is what a future UI surfacing ("1.2kg of this 5kg bag counted this week") would read from — without requiring another backend change when that's built.

### 5. No Celery ordering dependency needed

`update_pantry_post_order` (queue `pantry`) and `resolve_order_nutrition` (queue `nutrition`) are dispatched independently after `place` (`planning_graph.py:1172-1177`) with no ordering guarantee between them. This is fine as-is:

- `avg_weekly_consumption` reflects consumption **learned before this order** — `post_order_update`'s EMA refinement uses the *previous* `last_ordered_at`, not anything about this order's own future consumption. Reading the pre- or post-update value differs only by one EMA nudge (α = 0.2–0.4) — immaterial for this purpose.
- For a brand-new item, `PantryItem` doesn't exist yet regardless of which task wins the race — `estimate_consumed_g`'s fallback (no rate → full pack, today's behavior) is correct either way.

No chaining, no `.si()`/`.link()` Celery canvas changes required.

### 6. Non-food / unparseable-unit items

No special-casing. `compute_item_totals` already short-circuits to all-`None` when nutrition itself is unresolvable, and `estimate_consumed_g` no-ops (returns the full pack) when the consumption rate or its unit can't be parsed. Existing behavior is preserved everywhere the fix can't confidently improve on it.

### 7. Frontend contract update (`api.ts`)

`app/cockpit/src/lib/api.ts`'s `NutritionItemBreakdown` interface describes exactly the JSON shape `resolve_order_nutrition` writes into `item_breakdown`:

```ts
export interface NutritionItemBreakdown {
  ...
  quantity_g:  number | null
  ...
}
```

Section 4 renames that key to `pack_quantity_g` and adds `consumed_g`. Post-deploy, every new order's `item_breakdown` entries carry those two keys and no `quantity_g` at all — so this interface must be updated in the same change, even though nothing in `NutritionCard.tsx` currently reads `item.quantity_g` (only `calories`, `item_name`, `confidence` are rendered today, so there's no visible breakage either way). This matters because §4 explicitly frames these fields as what a **future UI surfacing** ("1.2kg of this 5kg bag counted this week") will read from — if that Phase 2 work is written against a stale `api.ts`, it'll reach for a field name that no longer exists in the data. Fix the type now, while the rename is fresh, not when Phase 2 goes looking for it:

```ts
export interface NutritionItemBreakdown {
  ...
  pack_quantity_g: number | null
  consumed_g:      number | null
  ...
}
```

---

## Worked Example

**Steady-state household, 5kg rice bag reordered periodically:**
`PantryItem.avg_weekly_consumption` learned as ~1.2kg/week → `consumed_g = min(5000, 1200) = 1200` → ~4,200 kcal attributed this week (at 350 kcal/100g) instead of ~17,500 kcal.

**First-ever order of that 5kg bag (no `PantryItem` yet):**
Falls back to the full 5000g — identical to today's behavior, once. `update_pantry_post_order` then creates the row with the `qty / 2` bootstrap default (2,500g/week) — so even the *second* order of the same pack is already capped, and keeps refining with more history.

**200g paneer pack, consumed within the week:**
`avg_weekly_consumption` ≈ pack size → `min()` returns the pack size unchanged → no behavior change. This fix should be a no-op for anything that isn't genuinely bulk relative to how fast it's used.

---

## Testing Plan

**Unit tests** (new, `tests/unit/test_nutrition_consumption_scaling.py` or added to the closest existing nutrition unit-test file):
- `estimate_consumed_g`: no `PantryItem`/no rate → returns full pack; rate < pack → capped correctly; rate > pack (e.g. a small pack with a high learned rate) → still can't exceed the pack itself, returns pack size; unparseable `consumption_unit` → falls back to full pack, no crash.
- `compute_item_totals`: confirms scaling uses `consumed_g` when supplied, and is unchanged when `consumed_g=None`.

**Integration test** (new, `tests/integration/`):
- Household with an established `PantryItem` (known `avg_weekly_consumption`) places an order for a large pack → resulting `OrderNutrition.total_calories`/`item_breakdown` reflects the capped estimate, not the full pack.
- Item never ordered before (no matching `PantryItem`) → resolves without error, falls back to current full-pack behavior for that one order.

**Regression check:** no existing test currently asserts on `compute_item_totals` or `resolve_order_nutrition` output directly (confirmed by search) — this is net-new coverage. `test_nutrition_backfill_convergence.py` and the Gap-to-Cart integration tests build `OrderNutrition` rows as fixtures and should be re-run once implemented, in case any of them incidentally assert on absolute totals.

## Rollout Notes

- Only orders placed **after** deploy get corrected numbers, per the explicit decision not to touch history. A household's weekly digest could show a visible drop in calorie/carb totals right after deploy if they've recently bought bulk staples — that's the fix working, not a regression, but worth knowing if anyone's watching dashboards closely across the deploy boundary.
- A backfill for historical `order_nutrition` rows is a reasonable follow-up once this has run in production for a while — not part of this task.
