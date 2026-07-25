# PRD — Nutrition Gap-to-Cart, Phase B3: Gap Detection & Recommendation API

**Status:** Ready for implementation
**Date:** 2026-07-24
**Branch:** `feature/nutrition-gap-detection`
**Series:** Gap-to-Cart phase B3 of 5 — see [`nutrition-gap-to-cart/implementation-plan.md`](../../docs/nutrition-gap-to-cart/implementation-plan.md).
**Depends on:**
  [Phase 0 — Schema](nutrition-gap-to-cart-phase0-schema.md),
  [Phase A — Targets UX layer](nutrition-gap-to-cart-phase-a-targets-ux.md) (and, transitively, [`personalised-nutrition-targets.md`](personalised-nutrition-targets.md) — targets this PRD measures gaps against),
  [Phase B1 — SKU enrichment](nutrition-gap-to-cart-phase-b1-sku-enrichment.md),
  [Phase B2 — Candidate map](nutrition-gap-to-cart-phase-b2-candidate-map.md)
**Blocks:** [Phase B4 — Digest & Gap-to-Cart UI](nutrition-gap-to-cart-phase-b4-digest-ui.md)

---

## Problem

`order_nutrition` (actuals) and `personalised_weekly_targets` (from the companion PRD) both exist, but nothing diffs them into an actionable shortfall, and nothing turns a shortfall into a buyable recommendation. This is the join: **weekly actual → personalized target → gap → ranked, diet-safe, in-stock SKUs → basket-ready.**

## Goals

- `GET /v1/nutrition/gaps` — the single endpoint B4's UI calls.
- Correct nutrient key resolution (see B1's canonical vocabulary — this is the PRD that would silently break without it).
- Correct target: read from `personalised_weekly_targets` (household total) via `per_member_targets` — **not** a separate calculation.
- A coverage guard so sparse micronutrient data (B12/vitamin D) never renders as a false deficiency.
- Live pricing — `nutrient_per_rupee` computed at request time, never read from a stored value.

## Out of Scope

- The UI that renders this endpoint's response (B4).
- Any change to `personalised-nutrition-targets.md`'s calculation — this PRD is a consumer of it, not a modifier.
- WhatsApp delivery of gaps (B4, later).

---

## Gap Diff

Weekly actuals: existing `order_nutrition` aggregation, already implemented in `GET /v1/nutrition/weekly` — reuse the query, don't duplicate it.

Weekly target: `personalised_weekly_targets(members, member_count)` from the companion PRD. **Read the household total, not a re-derivation** — this PRD does not compute targets, it only diffs against them.

```
gap[nutrient] = target[nutrient] - actual[nutrient]     # positive = short
```

Ranked worst-relative-gap first (`gap / target`, not raw magnitude — a 50g fiber shortfall matters more than a 50g calorie shortfall).

**Diet-specific watch list** — for `diet_type` in `{vegetarian, vegan, jain}`, always evaluate `b12` and `iron` even if the household hasn't set an explicit target for them (there is no target for micronutrients today; treat "zero source in the trailing window" as the trigger for these two, gated by the coverage guard below).

---

## Coverage Guard

**Required — this is a health-adjacent correctness issue, not a nice-to-have.** Per [Phase B1](nutrition-gap-to-cart-phase-b1-sku-enrichment.md#coverage-by-source--the-part-that-matters-for-correctness), `b12` and `vitamin_d` are emitted **only** by the LLM resolution path. A household whose week resolved mostly via OFF/USDA has no B12 data — that is "no data," not "no intake."

**Rule:** compute `coverage[nutrient] = (# of the week's resolved order items with a non-null value for that nutrient) / (# resolved items)`. Only emit a gap/deficiency for a nutrient when `coverage ≥ 0.6`. Below that, the API returns a distinct state:

```json
{ "nutrient": "b12", "status": "insufficient_data", "coverage": 0.31 }
```

instead of a `gap` entry. B4 renders this as "not enough data to assess" — never as a flagged shortfall. Applies to every micronutrient, not just B12/vitamin D, but is most consequential for those two given their narrow source coverage.

---

## Recommendation Pipeline

For each nutrient with a real gap (passed the coverage guard):

1. **Candidates** — query `nutrient_food_candidate` (via B2's query helper, which applies the seed-fallback rule) filtered by household `diet_type` and `allergies`. Pre-rank by `repurchase_rate × nutrient_per_100g` (both price-independent, per Phase 0/B2).
2. **Live search** — for the top ~8 candidate concepts, call Swiggy `search_products(food_concept)`. Keep only `in_stock` results.
3. **Live pricing** — compute `nutrient_per_rupee = (nutrient_per_100g × pack_grams / 100) / unit_price` from the **live** search price. Never read a stored per-rupee value (none exists — see Phase 0).
4. **Resolve** — for the top ~5 *by live per-rupee*, run through the existing OFF→USDA→Haiku chain (bounded; mostly cache hits once B1/B2 have been running a while).
5. **Final rank** — re-sort the resolved top 5 by the freshly-computed `nutrient_per_rupee`.
6. **Guardrails** — hard-exclude anything matching an `allergies` entry; hard-filter by `diet_type` (already applied in step 1, re-checked here as a safety net).

If resolution fails for an item in step 4, keep it in the response using the concept-level estimate from `nutrient_food_candidate`, marked at `confidence: "estimate"` — don't drop it silently.

---

## API Spec

### `GET /v1/nutrition/gaps`

**Auth:** household session required.

**Response:**

```json
{
  "success": true,
  "data": {
    "gaps": [
      {
        "nutrient": "protein",
        "status": "short",
        "target_weekly": 1624,
        "actual_weekly": 1400,
        "short_by": 224,
        "unit": "g",
        "recommendations": [
          {
            "sku_id": "swg_toor_dal_1kg_tata",
            "item_name": "Toor Dal 1kg",
            "brand": "Tata Sampann",
            "unit_price": 180,
            "delivers": 220,
            "per_rupee": 1.22,
            "confidence": "medium",
            "repurchase_rate": 0.61,
            "in_stock": true
          }
        ]
      },
      {
        "nutrient": "b12",
        "status": "insufficient_data",
        "coverage": 0.31
      }
    ],
    "computed_at": "2026-07-24T09:00:00Z"
  }
}
```

`status` ∈ `{"short", "insufficient_data", "on_track"}` — `on_track` nutrients are omitted from the response by default (only gaps and data-insufficient states are returned; keeps payload focused).

## Files to Change

| File | Change |
|---|---|
| `app/pilot/app/api/nutrition.py` | NEW route `GET /gaps`; reuses the weekly-actuals query already in `get_weekly_nutrition` |
| `app/pilot/app/services/nutrition_gaps.py` | NEW — gap diff, coverage guard, recommendation pipeline |
| `app/pilot/app/mcp/swiggy.py` | No change — reuses existing `search_products` |

## Definition of Done

- [ ] Gap diff reads `personalised_weekly_targets` directly — no parallel target calculation exists in this file.
- [ ] Test: a nutrient below 60% coverage returns `insufficient_data`, never a `gap` entry — specifically covering a B12-only-OFF-resolved fixture.
- [ ] Test (mock Swiggy MCP, `SWIGGY_RESPONSES`): protein gap → dal/paneer recommended; an allergen item excluded; a non-vegetarian item excluded for a `vegetarian` household.
- [ ] `per_rupee` in the response is traceably computed from the live search price in the request path — no code path reads a stored per-rupee value.
- [ ] Response omits `on_track` nutrients by default.
