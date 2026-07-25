# PRD — Nutrition Gap-to-Cart, Phase 0: Schema

**Status:** Ready for implementation
**Date:** 2026-07-24
**Branch:** `feature/nutrition-gap-schema`
**Series:** Gap-to-Cart phase 0 of 5 — see [`nutrition-gap-to-cart/implementation-plan.md`](../../docs/nutrition-gap-to-cart/implementation-plan.md) for the full arc.
**Depends on:** none (this is the foundation)
**Blocks:** [Phase B1](nutrition-gap-to-cart-phase-b1-sku-enrichment.md), [Phase B2](nutrition-gap-to-cart-phase-b2-candidate-map.md)

---

## Problem

Phases B1–B4 (SKU enrichment, the learned candidate map, gap detection, Gap-to-Cart UI) all need storage that doesn't exist yet:

- Nowhere to record what *food* a SKU actually is (`"Nandini Toned Milk"` → `milk`) or which nutrients it's a meaningful source of.
- No table for the learned nutrient → food ranking (Phase B2's whole output).
- No per-household way to dark-launch the feature.

This PRD is pure schema. No behavior changes. It exists so B1–B4 can each ship independently against a stable foundation instead of each doing its own ad-hoc migration.

## Goals

- Add the two `nutrition_cache` columns B1 needs to write to.
- Create `nutrient_food_candidate`, correctly indexed for the query B3 will actually run.
- Add the dark-launch flag column.
- Zero behavior change — every new column is nullable / defaulted, nothing reads them yet.

## Out of Scope

- Populating any of the new columns (that's B1 and B2).
- The canonical nutrient key vocabulary — that's a **code-level** contract (see B1/B3), not a schema concern; no column here encodes it.
- Any API or frontend change.

---

## Migration

One Alembic revision: `make migrate-new m="nutrition_gap_to_cart_schema"`.

### `nutrition_cache` — add 2 columns

| Column | Type | Default | Purpose |
|---|---|---|---|
| `food_concept` | `String(60)` | `NULL` | Canonical food, brand-stripped. `"Nandini Toned Milk"` → `"milk"`. Written by B1. |
| `notable_nutrients` | `JSONB` | `'[]'` | Nutrients this food is a meaningful source of, e.g. `["protein", "calcium"]`. Written by B1. |

Both nullable/defaulted — existing rows are valid with no backfill required by this migration (B1 owns backfill).

### `nutrient_food_candidate` — new table

The materialized output of Phase B2's nightly aggregation job.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `nutrient` | `String(20)` | Grouping key — **must** be one of the canonical values in the nutrient vocabulary table (see [Phase B1 PRD](nutrition-gap-to-cart-phase-b1-sku-enrichment.md#canonical-nutrient-vocabulary)). Not DB-enforced (no CHECK constraint); enforced by the shared `NUTRIENT_KEYS` constant + test. |
| `food_concept` | `String(60)` | Matches `nutrition_cache.food_concept`. |
| `diet_tags` | `ARRAY(String)` | e.g. `{vegetarian}`, `{vegetarian,vegan}`. |
| `nutrient_per_100g` | `Float` | Median across resolved SKUs of the concept. |
| `representative_sku_id` | `String(50)` | Most-ordered in-stock SKU for the concept. |
| `order_frequency` | `Integer`, default `0` | Count across `order_items`. |
| `repurchase_rate` | `Float`, nullable | Reorder signal, 0–1. |
| `confidence` | `String(10)` | Worst-case confidence of contributing `nutrition_cache` rows. |
| `sample_size` | `Integer`, default `0` | # SKUs / orders behind the row. |
| `last_refreshed` | `DateTime(timezone=True)`, server default `now()` | |

**No price column.** `nutrient_per_rupee` is computed at request time in B3 from a live Swiggy search price — there is no persistent price catalog to join here (`order_items.unit_price` is historical only). Storing a per-rupee figure in a nightly-refreshed table would rank recommendations on stale prices.

**Indexes** — `diet_tags` is queried by array containment (`diet_tags @> ARRAY['vegetarian']`) in B3. A plain composite btree does not serve containment:

```python
op.create_index("idx_nfc_nutrient", "nutrient_food_candidate", ["nutrient"])
op.create_index(
    "idx_nfc_diet_tags", "nutrient_food_candidate", ["diet_tags"],
    postgresql_using="gin",
)
```

### `households` — add 1 column

| Column | Type | Default | Purpose |
|---|---|---|---|
| `nutrition_gaps_enabled` | `Boolean` | `NULL` | Dark-launch flag for B3/B4. `NULL`/`false` = off. Toggled via `PATCH /v1/settings` (added in B4). |

---

## Files to Change

| File | Change |
|---|---|
| `app/pilot/migrations/versions/<rev>_nutrition_gap_to_cart_schema.py` | NEW — the migration above |
| `app/pilot/app/models/db.py` | Add the 2 columns to `NutritionCache`, the 1 column to `Household`, and the new `NutrientFoodCandidate` model |

## Rollback

`downgrade()` drops `nutrient_food_candidate` and the 3 added columns. Safe — nothing reads them until B1/B2/B4 ship.

## Definition of Done

- [ ] Migration applies cleanly on top of `g2h3i4j5k6l7` (the existing nutrition tables migration).
- [ ] `NutrientFoodCandidate` ORM model matches the table exactly, including the GIN index.
- [ ] `make migrate` + `make test` pass with no other code changes.
- [ ] Downgrade tested (`alembic downgrade -1` then re-upgrade).
