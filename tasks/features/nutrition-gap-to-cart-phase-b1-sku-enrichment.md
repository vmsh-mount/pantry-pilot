# PRD — Nutrition Gap-to-Cart, Phase B1: SKU Enrichment at Resolution

**Status:** Ready for implementation
**Date:** 2026-07-24
**Branch:** `feature/nutrition-gap-sku-enrichment`
**Series:** Gap-to-Cart phase B1 of 5 — see [`nutrition-gap-to-cart/implementation-plan.md`](../../docs/nutrition-gap-to-cart/implementation-plan.md).
**Depends on:** [Phase 0 — Schema](nutrition-gap-to-cart-phase0-schema.md)
**Blocks:** [Phase B2 — Candidate map](nutrition-gap-to-cart-phase-b2-candidate-map.md), [Phase B3 — Gap detection](nutrition-gap-to-cart-phase-b3-gap-detection.md)

---

## Problem

Recommending "something high in protein" requires knowing which *foods* — not SKUs — are protein sources. `nutrition_cache` is keyed by `sku_id` and has no notion of a food concept: "Nandini Toned Milk", "Milking A2 Pasteurised Milk", and "Heritage Daily Health Toned Milk" are three unrelated rows with no shared identity, even though they're all just `milk`.

Two things are needed before B2 can aggregate anything:
1. Every resolved SKU tagged with its canonical **food concept** (brand-stripped).
2. Every resolved SKU tagged with the nutrients it's a **meaningful source of**.

This PRD adds both, piggybacking on the resolution chain that already runs for every SKU.

## Goals

- Tag `food_concept` + `notable_nutrients` on every newly-resolved SKU, at near-zero added cost.
- Backfill the existing `nutrition_cache` population once.
- **Pin the canonical nutrient vocabulary** — the single most consequential contract in this whole feature, because getting it wrong fails silently downstream (an empty join in B3, not an error).

## Out of Scope

- Any change to the resolution chain's *confidence* logic (OFF → USDA → Haiku priority is untouched).
- The candidate map aggregation itself (B2).
- Any new API endpoint.

---

## Canonical Nutrient Vocabulary

**Read this section before writing any code in B1, B2, or B3.** There are two distinct key spaces already in the codebase, and a naive grouping key like `"b12"` matches neither:

| Space | Table.column | Example key | Populated by |
|---|---|---|---|
| Density (per 100g) | `nutrition_cache.nutrients` (JSONB) + first-class columns | `vitamin_b12_per_100g` | [`nutrition_resolution.py`](../../app/pilot/app/services/nutrition_resolution.py) — OFF / USDA / LLM |
| Totals (absolute, per order) | `order_nutrition.nutrient_totals` (JSONB) | `vitamin_b12_g` | [`compute_item_totals`](../../app/pilot/app/services/nutrition_resolution.py) via `k.replace("_per_100g", "_g")` |

**B2 reads the density space. B3's gap diff reads the totals space.** Both must resolve from the same grouping key.

### The table (declare once, import everywhere)

Add to `app/pilot/app/services/nutrition_resolution.py`:

```python
# Canonical nutrient grouping keys used across nutrition_cache, order_nutrition,
# and the Gap-to-Cart feature (nutrient_food_candidate, /v1/nutrition/gaps).
# Every consumer imports this — do not restate the mapping elsewhere.
NUTRIENT_KEYS: dict[str, dict[str, str]] = {
    "protein":        {"density": "protein_per_100g",        "total": "protein_g"},
    "fiber":          {"density": "fiber_per_100g",           "total": "fiber_g"},
    "sodium":         {"density": "sodium_mg_per_100g",       "total": "sodium_mg"},
    "iron":           {"density": "iron_per_100g",            "total": "iron_g"},
    "calcium":        {"density": "calcium_per_100g",         "total": "calcium_g"},
    "b12":            {"density": "vitamin_b12_per_100g",     "total": "vitamin_b12_g"},
    "vitamin_d":      {"density": "vitamin_d_per_100g",       "total": "vitamin_d_g"},
    "vitamin_c":      {"density": "vitamin_c_per_100g",       "total": "vitamin_c_g"},
    "potassium":      {"density": "potassium_per_100g",       "total": "potassium_g"},
    "sugar":          {"density": "sugar_per_100g",           "total": "sugar_g"},
    "saturated_fat":  {"density": "saturated_fat_per_100g",   "total": "saturated_fat_g"},
}
```

`notable_nutrients` (this PRD) and `nutrient_food_candidate.nutrient` (B2) and the `/v1/nutrition/gaps` response (B3) all use **only** keys from this dict's left column.

### Coverage by source — the part that matters for correctness

Not every nutrient is emitted by every source:

| Nutrient | Emitted by |
|---|---|
| `protein`, `fiber`, `sodium`, `sugar`, `saturated_fat` | OFF + USDA + LLM (all three) |
| `iron`, `calcium`, `potassium`, `vitamin_c` | USDA + LLM only |
| **`b12`, `vitamin_d`** | **LLM path only** — never OFF, never USDA |

A household whose items resolved entirely via OFF has **zero** B12/vitamin-D data. That is "no data," not "no intake" — B3 and B4 must treat it as such (see [Phase B3 PRD](nutrition-gap-to-cart-phase-b3-gap-detection.md#coverage-guard)). This PRD's job is just to make sure `notable_nutrients` is never asserted for a nutrient the source didn't actually resolve.

---

## Implementation

### 1. Extend the Haiku resolution prompt

`nutrition_resolution.py` already calls Haiku for `ESTIMATE`-confidence items. Extend that one prompt to also return:

```json
{ "food_concept": "paneer", "notable_nutrients": ["protein", "calcium"] }
```

Constraint on the prompt: `notable_nutrients` values **must** come from `NUTRIENT_KEYS`, and must only include a nutrient the resolution actually populated a non-null value for (no asserting `b12` on an OFF-only resolution — moot in practice since only the LLM path touches B12, but validate anyway).

### 2. OFF / USDA hits (no Haiku call)

Derive `food_concept` from `matched_name` via a cheap normalizer (strip brand tokens, lowercase, singularize) — no LLM call needed for the concept name itself. `notable_nutrients` for these rows: mark a nutrient notable if its density exceeds a simple per-nutrient floor (e.g. protein ≥ 5g/100g, iron ≥ 2mg/100g) — mechanical, not a judgment call.

### 3. One-time backfill

Maintenance-queue Celery task, batched, over existing `nutrition_cache` rows missing `food_concept`. Rate-limited against the Haiku API like the existing resolution calls.

### 4. Write path

Both `food_concept` and `notable_nutrients` persist to `nutrition_cache` on write, through both cache layers (Redis hot cache + DB), same as every other resolved field today.

---

## API Spec

No new endpoint. This PRD only changes what gets written during existing resolution calls.

## Files to Change

| File | Change |
|---|---|
| `app/pilot/app/services/nutrition_resolution.py` | Add `NUTRIENT_KEYS`; extend Haiku prompt + parsing; add `food_concept` normalizer for OFF/USDA path; add mechanical `notable_nutrients` derivation for non-LLM rows |
| `app/pilot/app/tasks/nutrition.py` | Persist the two new fields on cache write (both layers) |
| `app/pilot/app/tasks/maintenance.py` (or new task file) | One-time backfill task over existing `nutrition_cache` rows |

## Definition of Done

- [ ] `NUTRIENT_KEYS` is the single declared source of the grouping-key mapping; no other file restates it.
- [ ] Test: every key in `NUTRIENT_KEYS` resolves to a density key that actually appears in at least one resolved payload (guards the silent-empty-join failure mode).
- [ ] New resolutions write `food_concept` + `notable_nutrients`; `notable_nutrients` is never non-empty for a nutrient the source didn't populate.
- [ ] Backfill task run once against staging data; spot-check 20 rows for sane `food_concept` values (brand correctly stripped).
- [ ] No change to existing resolution confidence or caching behavior — this is additive-only.
