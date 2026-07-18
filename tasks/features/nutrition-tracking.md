# Nutrition Tracking
**Status: Pre-build · Branch: `feature/nutrition-tracking`**

---

## What This Is

Every PantryPilot order is ground-truth data about what a household eats. Nutrition tracking enriches that data with a full nutrient profile and surfaces it as a household health picture.

This is the feature that turns PantryPilot from "grocery automation" into "household health companion." It is also the foundation for smart substitution suggestions in the planning graph (V3).

---

## The Core Problem

Swiggy gives us: product name + brand + quantity description + SKU ID.
We need: a complete nutrient profile per unit purchased.

The gap is bridged by a three-layer resolution chain — Open Food Facts → USDA → Claude Haiku — with results cached per-SKU so each product is resolved only once, ever.

---

## Architecture Overview

```
Order placed
    │
    ▼
resolve_order_nutrition.delay(order_id)   ← Celery, nutrition queue, non-blocking
    │
    ├── For each order_item (concurrent):
    │       1. Redis cache (nutrition:{sku_id})  → hit: return immediately
    │       2. DB nutrition_cache table          → hit: backfill Redis, return
    │       3. Open Food Facts API              → packaged goods, HIGH/MEDIUM confidence
    │       4. USDA FoodData Central API        → fresh produce, raw ingredients, MEDIUM
    │       5. Claude Haiku                     → universal fallback, ESTIMATE confidence
    │       └── Write to Redis (30d TTL) + DB
    │
    └── Aggregate totals → write order_nutrition row
```

**Key invariant:** Nutrition resolution is always async, always post-order, never in the checkout path. A resolution failure never affects order placement.

---

## Confidence Tiers

Every resolved item carries a source and confidence. Never hidden, never over-claimed.

| Tier | Source | UI number | UI label |
|---|---|---|---|
| VERIFIED | User label scan / manual correction | `890 kcal` | "From your label" — highest trust, no disclaimer |
| HIGH | OFF barcode / near-exact match | `890 kcal` | "From label" |
| MEDIUM | OFF text match / USDA Foundation | `~620 kcal` | "Food database" |
| ESTIMATE | Claude Haiku | `~380 kcal` (muted italic) | "Estimated" |
| UNRESOLVED | All sources failed | `—` | "Not available" |

One-sentence disclaimer on every nutrition card: *"Figures are estimates based on product labels and food databases. Not a substitute for medical dietary advice."*

---

## Quantity Normalization

Swiggy's `quantityDescription` must be normalized to grams before scaling per-100g macro values.

| Pattern | Example | How |
|---|---|---|
| Weight | "1 kg", "500 g" | Direct parse |
| Volume | "1 L", "500 ml" | ml × density (water=1.0, milk=1.03, oil=0.92) |
| Count — known | "6 eggs", "12 bananas" | Hardcoded `UNIT_WEIGHTS_G` dict (~20 items) |
| Count — unknown | "4 pcs" (guava) | Haiku estimates unit weight |
| Ambiguous | "1 bunch spinach" | Haiku estimates (~200g typical) |

If quantity cannot be resolved, set `quantity_unresolvable = True` and exclude item from totals. The UI shows "N items excluded." We never silently assume a wrong weight.

---

## Data Model

### New tables (1 migration)

**`nutrition_cache`** — Per-SKU, global across all households. The same SKU resolved once is reused forever.

Design principle: fixed columns only for values we aggregate in SQL. Everything else goes in `nutrients JSONB` so new nutrient types, user-submitted corrections, and future data sources never require a migration.

```sql
CREATE TABLE nutrition_cache (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku_id                VARCHAR(50) UNIQUE NOT NULL,
    resolved_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Source + confidence
    source                VARCHAR(20) NOT NULL,
    -- 'off' | 'usda' | 'llm' | 'user_photo' | 'manual'
    -- user_photo: user submitted a label scan (future)
    -- manual: admin/data-team correction
    confidence            VARCHAR(10) NOT NULL,
    -- 'high' | 'medium' | 'estimate' | 'verified'
    -- verified: sourced from user photo or manual correction — highest trust

    -- Quantity normalization
    quantity_g            FLOAT,
    quantity_unresolvable BOOLEAN NOT NULL DEFAULT FALSE,
    serving_size_g        FLOAT,   -- declared serving size on label; needed for diabetic carbs-per-serving check

    -- Fixed columns: only the nutrients we GROUP BY / SUM in SQL queries.
    -- These power the weekly aggregation and compliance flag queries.
    calories_per_100g     FLOAT,
    protein_per_100g      FLOAT,
    total_carbs_per_100g  FLOAT,
    fat_per_100g          FLOAT,
    fiber_per_100g        FLOAT,
    sodium_mg_per_100g    FLOAT,   -- milligrams per 100g (sodium is never tracked in grams)

    -- All other nutrients live here. No migration needed to add a new one.
    -- Current keys: sugar, saturated_fat, trans_fat, cholesterol, calcium,
    --   iron, potassium, vitamin_c, vitamin_d, vitamin_b12, folate, zinc, omega3
    -- Future keys: anything — user-submitted, new sources, extended panels.
    -- All values are per 100g, numeric, null if unknown.
    nutrients             JSONB NOT NULL DEFAULT '{}',

    -- Source metadata
    nutriscore_grade      CHAR(1),
    matched_name          TEXT,
    off_product_id        TEXT,
    usda_fdc_id           INTEGER,
    raw_data              JSONB   -- full source API response, never discard
);
```

**Extensibility notes:**

- **User photo (future):** When a user scans a nutrition label, create or update the row with `source = 'user_photo'`, `confidence = 'verified'`, and merge the scanned values into `nutrients`. The fixed columns get updated too. A `verified` entry takes precedence over any auto-resolved entry in the read path.
- **Food diary (future):** Food logging per household member is a separate table (`member_food_logs`) that references `nutrition_cache.sku_id` — it does not need to change this schema at all.
- **New nutrients:** Just write a new key into `nutrients`. No ALTER TABLE, no migration, no code change to the resolution service — only the display layer needs updating.

**`order_nutrition`** — Per-order. One-to-one with `orders`. Written async after order is placed.

Same principle: fixed columns for the aggregates we query in SQL, JSONB for the full nutrient totals.

```sql
CREATE TABLE order_nutrition (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id              UUID UNIQUE NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    household_id          UUID NOT NULL REFERENCES households(id),
    computed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Fixed aggregates (used by weekly trends queries and compliance checks)
    total_calories        FLOAT,
    total_protein_g       FLOAT,
    total_carbs_g         FLOAT,
    total_fat_g           FLOAT,
    total_fiber_g         FLOAT,
    total_sodium_mg       FLOAT,

    -- All other nutrient totals (sugar, saturated fat, calcium, iron, B12, etc.)
    -- Mirrors the keys in nutrition_cache.nutrients, summed across items.
    -- Extend here for free whenever nutrition_cache.nutrients gains a new key.
    nutrient_totals       JSONB NOT NULL DEFAULT '{}',

    -- Resolution coverage
    total_items           INTEGER NOT NULL,
    resolved_items        INTEGER NOT NULL,
    high_confidence_items INTEGER NOT NULL DEFAULT 0,
    llm_estimated_items   INTEGER NOT NULL DEFAULT 0,
    unresolved_items      INTEGER NOT NULL DEFAULT 0,

    -- Per-item breakdown for card reads — no join required
    -- Each entry: {item_name, sku_id, source, confidence, quantity_g,
    --              calories, protein_g, carbs_g, fat_g, fiber_g, sodium_mg,
    --              nutrients: {...full JSONB from nutrition_cache}}
    item_breakdown        JSONB NOT NULL DEFAULT '[]'
);
```

**`member_food_logs`** *(future — not built now)* — Per-member daily food diary. References `nutrition_cache.sku_id` for items scanned or logged manually. No schema change to `nutrition_cache` required when this is built.
```sql
-- Placeholder — design deferred to food diary feature
-- CREATE TABLE member_food_logs (
--     id             UUID PRIMARY KEY,
--     household_id   UUID REFERENCES households(id),
--     member_ref     VARCHAR,        -- opaque member identifier (no PII table yet)
--     logged_at      TIMESTAMPTZ,
--     meal_type      VARCHAR,        -- 'breakfast' | 'lunch' | 'dinner' | 'snack'
--     sku_id         VARCHAR REFERENCES nutrition_cache(sku_id),
--     custom_item    TEXT,           -- free-text for items not in nutrition_cache
--     quantity_g     FLOAT,
--     source         VARCHAR         -- 'pantry_pilot_item' | 'user_photo' | 'manual'
-- );
```

**`household_nutrition_goals`** — Optional, one per household. Overrides ICMR RDA defaults.
```
household_id UUID UNIQUE FK   daily_calories INTEGER
daily_protein_g INTEGER        daily_fiber_g INTEGER
daily_sodium_mg INTEGER        updated_at TIMESTAMPTZ
```

---

## External APIs

### Open Food Facts
```
GET https://world.openfoodfacts.org/cgi/search.pl
  ?search_terms={brand}+{product_name}
  &json=1&page_size=5
  &fields=product_name,brands,nutriments,nutriscore_grade,quantity

Match scoring:
  Exact brand match:              +2 pts
  Token overlap ≥ 80% on name:   +3 pts
  nutriments.energy-kcal_100g present: required
  Score ≥ 4 → HIGH confidence
  Score ≥ 3 → MEDIUM confidence
  Score < 3 → fall through

Timeout: 3s. No auth required.
```

### USDA FoodData Central
```
GET https://api.nal.usda.gov/fdc/v1/foods/search
  ?query={item_name_without_brand}
  &dataType=Foundation,SR%20Legacy
  &pageSize=5
  &api_key={USDA_API_KEY}

Prefer Foundation data type. First result where foodCategory is plausible.
All USDA matches → MEDIUM confidence.
Best for: onion, tomato, spinach, rice, atta, milk, eggs.
Env var: USDA_API_KEY (free, register at api.nal.usda.gov)
```

### Claude Haiku (fallback)
```python
NUTRITION_PROMPT = """Estimate nutrients per 100g for this food sold on Swiggy Instamart India.
Item: {item_name}
Brand: {brand or "generic"}
Quantity as sold: {qty_desc}

Return ONLY valid JSON. Use null for values you cannot reasonably estimate.
Use published nutritional data, ICMR tables, or USDA averages as your source.

{{
  "calories_per_100g": 0,
  "protein_per_100g": 0,
  "total_carbs_per_100g": 0,
  "sugar_per_100g": 0,
  "fat_per_100g": 0,
  "saturated_fat_per_100g": 0,
  "fiber_per_100g": 0,
  "sodium_mg_per_100g": 0,
  "calcium_per_100g": null,
  "iron_per_100g": null,
  "vitamin_b12_per_100g": null,
  "vitamin_d_per_100g": null
}}"""

Model: claude-haiku-4-5-20251001  Temperature: 0.1
Macronutrients: always estimable. Micronutrients: null if uncertain — better than wrong.
Cost: ~₹0.01/item. Cold miss on 15-item order ≈ ₹0.15. Near-zero after cache warms.
```

---

## New Files

```
app/pilot/app/
  services/
    nutrition_resolution.py     ← resolution chain service (OFF + USDA + LLM + cache)
  tasks/
    nutrition.py                ← Celery tasks (resolve_order_nutrition, weekly_digest)
  api/
    nutrition.py                ← API routes (/v1/nutrition/*)
  models/db.py                  ← add NutritionCache, OrderNutrition, HouseholdNutritionGoals
  migrations/
    XXXX_nutrition_tables.py    ← alembic migration

app/cockpit/src/
  components/nutrition/
    NutritionCard.tsx           ← per-order card (calories, macro bars, per-item list)
    WeeklyNutritionPanel.tsx    ← weekly trends (V2)
    ComplianceFlag.tsx          ← diet compliance banner (V2)
  app/
    flow/page.tsx               ← add NutritionCard to order history detail
    quick/page.tsx              ← add NutritionCard to confirmed screen
```

---

## API Routes

```
GET  /v1/nutrition/order/{order_id}
     200: {order_id, computed_at, total_calories, total_protein_g, total_carbs_g,
           total_fat_g, total_fiber_g, total_sodium_mg, total_items,
           resolved_items, unresolved_items, llm_estimated_items, item_breakdown}
     202: {status: "computing", retry_after: 10}   ← task not done yet
     404: not found / not owned by session

GET  /v1/nutrition/weekly?weeks=4
     [{week_start, total_calories, total_protein_g, total_carbs_g,
       total_fat_g, total_fiber_g, total_sodium_mg, order_count}]

GET  /v1/nutrition/compliance
     [{flag_type, severity, items: [{item_name, value, threshold}]}]

PATCH /v1/nutrition/goals
      body: {daily_calories, daily_protein_g, daily_fiber_g, daily_sodium_mg}
      → upsert household_nutrition_goals
```

---

## Celery Tasks

New queue: `nutrition`. Add to `docker-compose.yml` worker queues.

```python
# app/tasks/nutrition.py

resolve_order_nutrition(order_id: str)
  Queue: nutrition · max_retries: 3 · retry_delay: 30s · idempotent
  Triggered from:
    - planning_graph.py → place node (after db.commit)
    - quick.py → checkout endpoint (after db.commit)

compute_weekly_compliance(household_id: str)
  Queue: nutrition
  Triggered by Beat: Sunday 5 PM IST (11:30 UTC)
  Writes compliance flags; used by dashboard and (later) WhatsApp integration
```

Beat additions in `app/worker.py`:
```python
"nutrition-weekly-compliance": {
    "task": "app.tasks.nutrition.trigger_all_compliance",
    "schedule": crontab(hour=11, minute=30, day_of_week=0),
},
```

`trigger_all_compliance` definition (fan-out task — the Beat task itself must be defined):
```python
@celery_app.task(name="app.tasks.nutrition.trigger_all_compliance")
def trigger_all_compliance():
    """Beat entry-point: fan out per-household compliance computation."""
    # Celery tasks are sync; use the same asyncio run pattern as other tasks
    # in this codebase (check existing tasks for the project's canonical wrapper).
    import asyncio
    async def _run():
        async with get_db_session() as db:
            result = await db.execute(
                select(Household.id).where(Household.is_active == True)
            )
            return result.scalars().all()
    household_ids = asyncio.get_event_loop().run_until_complete(_run())
    for hh_id in household_ids:
        compute_weekly_compliance.delay(str(hh_id))
```
**Note for implementation:** Verify the async-in-sync wrapper pattern against existing tasks in `app/tasks/` (e.g. `planning.py`) before wiring — the project may already have a helper like `run_async()` or use `asyncio.run()` instead.

---

## Planning Graph Integration

### V1 trigger (minimal change)
In `planning_graph.py`, `place` node — after `db.commit()`:
```python
from app.tasks.nutrition import resolve_order_nutrition
resolve_order_nutrition.delay(str(order.id))
```

### V2 — nutrition-aware plan_llm
The `plan_llm` node system prompt is extended with the household's trailing-7-day nutrition summary. This closes the loop: nutrition data actively influences what gets planned.

```python
# In plan_llm node, fetch from order_nutrition:
nutrition_context = """
Household nutrition this week (trailing 7 days):
  Calories:  {calories} / {weekly_target}  {flag}
  Protein:   {protein}g / {target}g        {flag}
  Fiber:     {fiber}g / {target}g          {flag}
  Sodium:    {sodium}mg / {target}mg       {flag}

When suggesting items: address gaps (low protein/fiber), avoid worsening excesses (sodium).
"""
```

**V2 aggregation query** (run in `plan_llm` node before LLM call):
```sql
-- Trailing 7-day nutrition summary for a household
SELECT
    SUM(onut.total_calories)   AS calories,
    SUM(onut.total_protein_g)  AS protein_g,
    SUM(onut.total_fiber_g)    AS fiber_g,
    SUM(onut.total_sodium_mg)  AS sodium_mg,
    SUM(onut.total_carbs_g)    AS carbs_g,
    SUM(onut.total_fat_g)      AS fat_g,
    COUNT(*)                   AS order_count
FROM order_nutrition onut
JOIN orders o ON o.id = onut.order_id
WHERE onut.household_id = :household_id
  AND o.placed_at >= NOW() - INTERVAL '7 days';
```

Weekly targets are fetched from `household_nutrition_goals` (if set) or computed from `households.member_count` using the ICMR defaults table. Gap/excess flags are computed in Python before injecting into the prompt string.

### V3 — smart substitutions in optimize node
The `optimize` node scores SKU candidates by nutritional alignment with the household gap profile. Prefers toned milk over full-fat if calories are over target, multigrain bread if fiber is low, etc. Substitution reason stored in `loop_run_items` for display in basket preview.

---

## Diet Compliance Flags (V2)

Driven by the existing `diet_type` field on `households`. No new user input required.

| diet_type | Flag condition |
|---|---|
| `diabetic-friendly` | `sugar_per_100g > 20` OR `(total_carbs_per_100g / 100 * serving_size_g) > 60` (carbs per declared serving; falls back to per-100g check when `serving_size_g` is NULL) |
| `low-sodium` | `sodium_mg_per_100g > 600` (WHO threshold: 600 mg per 100g) |
| `heart-healthy` | `saturated_fat_per_100g > 5` |
| `high-protein` | weekly `total_protein_g < 0.15 × total_calories / 4` (protein under 15% of calories) |

Flags surface on the nutrition card and on the weekly dashboard panel. They are informational only — never block ordering.

---

## Household Nutrition Targets (ICMR RDA defaults)

Auto-computed from `member_count`. No user input required for V1.

| member_count | Daily calories | Protein | Fiber | Sodium |
|---|---|---|---|---|
| 1 | 2,000 kcal | 50g | 25g | 2,300mg |
| 2 | 4,000 kcal | 100g | 50g | 4,600mg |
| 3 | 5,100 kcal | 126g | 63g | 5,400mg |
| 4+ | count × 1,700 | count × 42g | count × 21g | count × 1,800mg |

Weekly target = daily × 7. Households can override in Settings (V2, `household_nutrition_goals` table).

---

## Frontend: NutritionCard Component (V1)

**Location:** `app/cockpit/src/components/nutrition/NutritionCard.tsx`

**Used in:**
- Flow order history item detail (expandable below order summary)
- Quick Order confirmed screen (below receipt, loads async)

**States:**
- `loading` — spinner while Celery task runs (poll `GET /v1/nutrition/order/{id}` every 10s on 202, matching `retry_after` in response)
- `loaded` — full card with macro bars + per-item list
- `error` — "Nutrition data unavailable" (silent fallback, never blocking)

**Per-item row:** item name · confidence badge · calorie value
- VERIFIED: ✓ checkmark badge, no "~" prefix, bold — highest trust, no disclaimer
- HIGH: no badge prefix, normal weight
- MEDIUM: "~" prefix, normal weight
- ESTIMATE: "~" prefix, muted color, italic
- UNRESOLVED: "—", muted

**Footer:** "N of M items resolved · Figures are estimates. Not medical advice."

"Report incorrect data" link → simple feedback form (log to DB, no complex flow in V1).

---

## New Environment Variables

```
USDA_API_KEY=    # Free, register at api.nal.usda.gov
```

---

## Implementation Sequence

### Phase 1 — Foundation + per-order card
1. DB migration: `nutrition_cache`, `order_nutrition`, `household_nutrition_goals`
2. `nutrition_resolution.py` — resolution chain with OFF, USDA, LLM, caching
3. `nutrition.py` Celery task — `resolve_order_nutrition`, nutrition queue
4. Wire trigger into `planning_graph.place` and `quick.checkout`
5. `GET /v1/nutrition/order/{order_id}` API route
6. `NutritionCard.tsx` component — polling, macro bars, per-item confidence rows
7. Integrate into Flow order history + Quick Order confirmed screen

### Phase 2 — Weekly trends + diet compliance
8. `GET /v1/nutrition/weekly` and `GET /v1/nutrition/compliance` routes
9. `WeeklyNutritionPanel.tsx` on dashboard
10. `ComplianceFlag.tsx` — driven by `diet_type` thresholds
11. Beat task for weekly compliance computation
12. `PATCH /v1/nutrition/goals` + Settings UI for goal override
13. `plan_llm` nutrition context injection

### Phase 3 — Smart substitutions
14. `optimize` node: score SKU candidates by nutritional alignment
15. Substitution reason in `loop_run_items`, badge in basket preview

---

## Out of Scope

- Meal planning or cooking inference (we track purchases, not meals)
- Individual family member tracking (household totals only)
- Restaurant order nutrition (Instamart grocery only)
- Fitness wearable integration
- Medical dietary advice (explicitly disclaimed)
- Barcode scanning (Swiggy doesn't expose barcodes)
