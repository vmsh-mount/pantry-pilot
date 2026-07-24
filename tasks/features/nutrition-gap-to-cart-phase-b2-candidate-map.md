# PRD — Nutrition Gap-to-Cart, Phase B2: Learned Candidate Map

**Status:** Ready for implementation
**Date:** 2026-07-24
**Branch:** `feature/nutrition-gap-candidate-map`
**Series:** Gap-to-Cart phase B2 of 5 — see [`nutrition-gap-to-cart/implementation-plan.md`](../../docs/nutrition-gap-to-cart/implementation-plan.md).
**Depends on:** [Phase 0 — Schema](nutrition-gap-to-cart-phase0-schema.md), [Phase B1 — SKU enrichment](nutrition-gap-to-cart-phase-b1-sku-enrichment.md)
**Blocks:** [Phase B3 — Gap detection](nutrition-gap-to-cart-phase-b3-gap-detection.md)

---

## Problem

B3 needs to answer "what foods deliver protein, for a vegetarian household?" without either (a) a hand-maintained list that goes stale and can't reflect what this market actually buys, or (b) querying `nutrition_cache` live on every request (slow, and most SKUs are still unresolved).

The answer is a materialized, periodically-refreshed table — distilled from two systems already running: the resolution chain (B1's output) and the order log. This is explicitly **not** a static table: it's rebuilt nightly from real data, with a shrinking seed floor for cold start only.

## Goals

- Nightly job that aggregates `nutrition_cache` (post-B1) + `order_items` into `nutrient_food_candidate`.
- Rank signal — `repurchase_rate` — that a hand-written list could never have, because it's an observed behavior, not a nutritional fact.
- Cold-start floor that ships once and shrinks to irrelevance as real data accrues; never hand-edited after ship.

## Out of Scope

- Anything involving price. `nutrient_per_rupee` is **not** computed here — see Phase 0's schema note. This table holds only price-independent signals.
- The gap detection / recommendation logic that consumes this table (B3).

---

## The Job — `rebuild_nutrient_food_map`

Celery beat, maintenance queue, nightly.

```
1. Group nutrition_cache by food_concept (post-B1 enrichment).
   For each concept, per nutrient in NUTRIENT_KEYS:
     - nutrient_per_100g = median(density value across contributing SKUs)
       (median, not mean — robust to one bad OFF match skewing the concept)
     - confidence = worst-case confidence among contributing rows
     - sample_size = count of contributing SKUs

2. Join order_items (last 90 days) grouped by food_concept:
     - order_frequency = count of order_items for this concept
     - repurchase_rate  = fraction of households with ≥2 orders of this concept
     - representative_sku_id = most-ordered in-stock SKU for the concept

3. Explode step 1 by each concept's notable_nutrients (from B1) into
   (nutrient, food_concept) rows. Tag diet_tags by checking the concept
   name against a small non-veg exclusion list (meat/fish/egg keywords) —
   everything else is tagged {vegetarian}; a curated {vegan} subtag
   excludes dairy/egg-adjacent concepts.

4. Upsert into nutrient_food_candidate on (nutrient, food_concept).
   Stamp last_refreshed = now().
```

### Cold-start seed floor

Ship `seed_nutrient_foods.json` — ~15 well-known foods per nutrient (paneer/dal/curd for protein, spinach/rajma for iron, curd/milk/fortified cereal for B12, etc.), each with an approximate `nutrient_per_100g` from public nutrition tables.

**Rule, applied at query time (not by mutating the table):** for a given `(nutrient, diet)`, if `nutrient_food_candidate` has `≥ N` rows with `sample_size ≥ M` (thresholds TBD during implementation, suggest `N=3, M=5`), use the learned rows. Otherwise fall back to the seed for that nutrient/diet pair only.

This is a **bootstrap, not a maintained artifact** — it ships once, is never hand-edited after, and the rule above means it silently stops mattering once the learned table has enough coverage. It is not synced into `nutrient_food_candidate`; it lives as a separate static resource the query layer consults as a fallback.

---

## Ranking inputs available to B3

This table exposes, per `(nutrient, food_concept)`:
- `nutrient_per_100g` — nutritional fact
- `repurchase_rate`, `order_frequency` — observed household behavior
- `confidence`, `sample_size` — honesty about data quality

B3 combines these with a live price to rank. Nothing here is price-aware, by design (Phase 0 schema note).

---

## API Spec

No API endpoint — this is a background job. Internal visibility only: log `rows_upserted`, `rows_from_seed_fallback` (i.e. nutrient/diet pairs still below threshold) per run, so seed-reliance is observable and its shrinkage over time is visible in logs/metrics rather than assumed.

## Files to Change

| File | Change |
|---|---|
| `app/pilot/app/tasks/nutrition.py` | NEW task `rebuild_nutrient_food_map`, maintenance queue |
| `app/pilot/app/tasks/celery_app.py` (beat schedule) | Nightly schedule entry |
| `app/pilot/app/data/seed_nutrient_foods.json` | NEW — the cold-start seed floor |
| `app/pilot/app/services/nutrient_candidates.py` | NEW — query helper implementing the seed-fallback rule, imported by B3 |

## Definition of Done

- [ ] Nightly job upserts `nutrient_food_candidate` without duplicate `(nutrient, food_concept)` rows.
- [ ] Median aggregation verified robust to a synthetic outlier (one bad OFF match doesn't skew the concept's density).
- [ ] Seed fallback triggers only below the `(N, M)` threshold; test asserts a well-populated `(nutrient, diet)` pair does **not** fall back.
- [ ] `rows_from_seed_fallback` logged per run — visible signal that the seed is shrinking in relevance, not something asserted without evidence.
- [ ] No `nutrient_per_rupee` or any price field written to this table.
