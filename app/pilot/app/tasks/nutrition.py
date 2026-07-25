"""
Celery tasks for nutrition resolution and compliance.

Queue: nutrition
"""

import asyncio
import uuid

from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="nutrition",
    name="app.tasks.nutrition.resolve_order_nutrition",
)
def resolve_order_nutrition(self, order_id: str):
    """
    Resolve nutrition for every item in an order and write order_nutrition row.
    Triggered after checkout (planning_graph place node + quick.py checkout).
    Idempotent: upserts order_nutrition by order_id.
    """
    async def _run():
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models.db import Order, OrderItem, OrderNutrition
        from app.services.nutrition_resolution import resolve_item, compute_item_totals

        async with AsyncSessionLocal() as db:
            # Fetch order + items
            order_result = await db.execute(
                select(Order).where(Order.id == order_id)
            )
            order = order_result.scalar_one_or_none()
            if not order:
                logger.warning("resolve_nutrition_order_not_found", order_id=order_id)
                return

            items_result = await db.execute(
                select(OrderItem).where(OrderItem.order_id == order_id)
            )
            items = items_result.scalars().all()

            if not items:
                logger.info("resolve_nutrition_no_items", order_id=order_id)
                return

            # Resolve each item (sequentially to avoid rate-limiting OFF/USDA)
            item_breakdown = []
            totals = {
                "calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0,
                "fat_g": 0.0, "fiber_g": 0.0, "sodium_mg": 0.0,
            }
            nutrient_totals: dict[str, float] = {}
            resolved_count = 0
            high_conf_count = 0
            llm_count = 0
            unresolved_count = 0

            for item in items:
                qty_desc = f"{float(item.quantity)} {item.unit}" if item.quantity and item.unit else ""
                resolved = await resolve_item(
                    db=db,
                    sku_id=item.swiggy_sku_id or f"unknown_{item.product_name}",
                    item_name=item.product_name,
                    brand=item.brand,
                    qty_desc=qty_desc,
                )

                confidence = resolved.get("confidence", "unresolved")
                if confidence == "unresolved":
                    unresolved_count += 1
                else:
                    resolved_count += 1
                    if confidence in ("high", "verified"):
                        high_conf_count += 1
                    elif confidence == "estimate":
                        llm_count += 1

                scaled = compute_item_totals(resolved)

                # Accumulate totals (skip None values)
                for key in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg"):
                    if scaled.get(key) is not None:
                        totals[key] = round(totals[key] + scaled[key], 1)

                for k, v in (scaled.get("nutrient_totals") or {}).items():
                    if v is not None:
                        nutrient_totals[k] = round(nutrient_totals.get(k, 0.0) + v, 1)

                item_breakdown.append({
                    "item_name": item.product_name,
                    "sku_id": item.swiggy_sku_id,
                    "source": resolved.get("source"),
                    "confidence": confidence,
                    "quantity_g": resolved.get("quantity_g"),
                    "calories": scaled.get("calories"),
                    "protein_g": scaled.get("protein_g"),
                    "carbs_g": scaled.get("carbs_g"),
                    "fat_g": scaled.get("fat_g"),
                    "fiber_g": scaled.get("fiber_g"),
                    "sodium_mg": scaled.get("sodium_mg"),
                    "nutrients": scaled.get("nutrient_totals") or {},
                })

            # Upsert order_nutrition
            existing = await db.execute(
                select(OrderNutrition).where(OrderNutrition.order_id == order_id)
            )
            on = existing.scalar_one_or_none()
            fields = dict(
                household_id=order.household_id,
                total_calories=totals["calories"],
                total_protein_g=totals["protein_g"],
                total_carbs_g=totals["carbs_g"],
                total_fat_g=totals["fat_g"],
                total_fiber_g=totals["fiber_g"],
                total_sodium_mg=totals["sodium_mg"],
                nutrient_totals=nutrient_totals,
                total_items=len(items),
                resolved_items=resolved_count,
                high_confidence_items=high_conf_count,
                llm_estimated_items=llm_count,
                unresolved_items=unresolved_count,
                item_breakdown=item_breakdown,
            )
            if on:
                for k, v in fields.items():
                    setattr(on, k, v)
            else:
                on = OrderNutrition(id=str(uuid.uuid4()), order_id=order_id, **fields)
                db.add(on)

            await db.commit()
            logger.info(
                "order_nutrition_resolved",
                order_id=order_id,
                total_items=len(items),
                resolved=resolved_count,
                unresolved=unresolved_count,
            )

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error("resolve_order_nutrition_failed", order_id=order_id, error=str(e))
        raise self.retry(exc=e)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="nutrition",
    name="app.tasks.nutrition.compute_weekly_compliance",
)
def compute_weekly_compliance(self, household_id: str):
    """
    Compute diet compliance flags for a household based on their trailing
    4-week purchase history. Called by trigger_all_compliance Beat task.
    Writes results to Redis for fast API reads.
    """
    async def _run():
        import json
        from sqlalchemy import select, func as sqlfunc
        from app.database import AsyncSessionLocal
        from app.models.db import Household, OrderNutrition, Order, NutritionCache, OrderItem, HouseholdNutritionGoals
        from app.redis import get_redis

        async with AsyncSessionLocal() as db:
            hh_result = await db.execute(
                select(Household).where(Household.id == household_id)
            )
            hh = hh_result.scalar_one_or_none()
            if not hh:
                return

            diet_type = hh.diet_type or ""
            flags = []

            if "diabetic" in diet_type or "low-sodium" in diet_type or "heart" in diet_type or "high-protein" in diet_type:
                # Fetch recent order items with their nutrition data
                from datetime import datetime, timedelta
                cutoff = datetime.utcnow() - timedelta(days=28)

                items_result = await db.execute(
                    select(OrderItem, NutritionCache)
                    .join(Order, Order.id == OrderItem.order_id)
                    .outerjoin(NutritionCache, NutritionCache.sku_id == OrderItem.swiggy_sku_id)
                    .where(Order.household_id == household_id)
                    .where(Order.placed_at >= cutoff)
                )
                pairs = items_result.all()

                flagged_items_diabetic = []
                flagged_items_sodium = []
                flagged_items_sat_fat = []

                total_calories = 0.0
                total_protein_g = 0.0

                for order_item, nc in pairs:
                    if nc is None:
                        continue

                    # Diabetic: sugar > 20 OR carbs per serving > 60
                    if "diabetic" in diet_type:
                        sugar = (nc.nutrients or {}).get("sugar_per_100g")
                        if sugar is not None and sugar > 20:
                            flagged_items_diabetic.append({
                                "item_name": order_item.product_name,
                                "value": sugar,
                                "threshold": 20,
                                "field": "sugar_per_100g",
                            })
                        elif nc.total_carbs_per_100g and nc.serving_size_g:
                            carbs_per_serving = nc.total_carbs_per_100g / 100 * nc.serving_size_g
                            if carbs_per_serving > 60:
                                flagged_items_diabetic.append({
                                    "item_name": order_item.product_name,
                                    "value": round(carbs_per_serving, 1),
                                    "threshold": 60,
                                    "field": "carbs_per_serving_g",
                                })

                    # Low-sodium: sodium > 600 mg/100g
                    if "low-sodium" in diet_type and nc.sodium_mg_per_100g is not None:
                        if nc.sodium_mg_per_100g > 600:
                            flagged_items_sodium.append({
                                "item_name": order_item.product_name,
                                "value": nc.sodium_mg_per_100g,
                                "threshold": 600,
                                "field": "sodium_mg_per_100g",
                            })

                    # Heart-healthy: saturated fat > 5g/100g
                    if "heart" in diet_type:
                        sat_fat = (nc.nutrients or {}).get("saturated_fat_per_100g")
                        if sat_fat is not None and sat_fat > 5:
                            flagged_items_sat_fat.append({
                                "item_name": order_item.product_name,
                                "value": sat_fat,
                                "threshold": 5,
                                "field": "saturated_fat_per_100g",
                            })

                    # High-protein tracking
                    if "high-protein" in diet_type and nc.calories_per_100g and nc.protein_per_100g:
                        qty_g = getattr(order_item, "quantity", 1) or 1
                        total_calories += nc.calories_per_100g * qty_g / 100
                        total_protein_g += nc.protein_per_100g * qty_g / 100

                if flagged_items_diabetic:
                    flags.append({"flag_type": "diabetic_sugar", "severity": "warning", "items": flagged_items_diabetic[:5]})
                if flagged_items_sodium:
                    flags.append({"flag_type": "high_sodium", "severity": "warning", "items": flagged_items_sodium[:5]})
                if flagged_items_sat_fat:
                    flags.append({"flag_type": "high_saturated_fat", "severity": "info", "items": flagged_items_sat_fat[:5]})
                if "high-protein" in diet_type and total_calories > 0:
                    protein_pct = (total_protein_g * 4) / total_calories
                    if protein_pct < 0.15:
                        flags.append({
                            "flag_type": "low_protein",
                            "severity": "info",
                            "items": [],
                            "detail": f"Protein is {round(protein_pct * 100, 1)}% of calories (target ≥ 15%)",
                        })

            redis = await get_redis()
            await redis.set(
                f"compliance:{household_id}",
                json.dumps(flags),
                ex=60 * 60 * 24 * 7,  # 7-day TTL
            )
            logger.info("compliance_computed", household_id=household_id, flags=len(flags))

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error("compute_compliance_failed", household_id=household_id, error=str(e))
        raise self.retry(exc=e)


@celery_app.task(
    queue="nutrition",
    name="app.tasks.nutrition.trigger_all_compliance",
)
def trigger_all_compliance():
    """Beat entry-point: fan out compliance computation to all active households."""
    async def _get_ids():
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models.db import Household
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Household.id).where(Household.is_active == True)
            )
            return result.scalars().all()

    household_ids = asyncio.run(_get_ids())

    for hh_id in household_ids:
        compute_weekly_compliance.delay(str(hh_id))

    logger.info("compliance_fanout_dispatched", count=len(household_ids))


# ══════════════════════════════════════════════════════════════════════════════
# Gap-to-Cart Phase B2 — learned nutrient -> food candidate map
# ══════════════════════════════════════════════════════════════════════════════

# Confidence rank for "worst-case confidence among contributing rows" — lower wins.
_CONF_RANK = {"unresolved": 0, "estimate": 1, "medium": 2, "high": 3, "verified": 4}

# Concepts that are dairy (vegetarian but not vegan). Egg is already excluded
# from vegetarian entirely via _NON_VEG_KEYWORDS (this codebase's convention,
# see app.agent.planning_graph), so it never reaches this list.
_DAIRY_CONCEPTS = frozenset({
    "milk", "curd", "paneer", "cheese", "ghee", "butter", "yogurt", "cream", "khoya",
})

_ORDER_ITEMS_LOOKBACK_DAYS = 90


def _diet_tags_for_concept(concept: str) -> list[str]:
    """
    vegetarian: everything except a non-veg concept (reuses the same
    exclusion list planning_graph.py already uses for basket filtering, so
    this feature's notion of "vegetarian" can't drift from the app's).
    jain: vegetarian minus root vegetables (_JAIN_BLOCK).
    vegan: vegetarian minus dairy (_DAIRY_CONCEPTS) — egg is already excluded
    upstream since it's non-veg in this codebase's convention.
    """
    from app.agent.planning_graph import _NON_VEG_KEYWORDS, _JAIN_BLOCK

    if concept in _NON_VEG_KEYWORDS:
        return []
    tags = ["vegetarian"]
    if concept not in _JAIN_BLOCK:
        tags.append("jain")
    if concept not in _DAIRY_CONCEPTS:
        tags.append("vegan")
    return tags


async def _upsert_candidate(db, nutrient: str, food_concept: str, diet_tags: list[str],
                             nutrient_per_100g: float, representative_sku_id: str | None,
                             order_frequency: int, repurchase_rate: float | None,
                             confidence: str | None, sample_size: int) -> None:
    """
    Application-level upsert by (nutrient, food_concept) — there's no DB
    unique constraint on that pair (Phase 0 didn't add one), so idempotency
    is enforced here: select existing row, update in place, or insert.
    """
    from sqlalchemy import select
    from app.models.db import NutrientFoodCandidate

    result = await db.execute(
        select(NutrientFoodCandidate).where(
            NutrientFoodCandidate.nutrient == nutrient,
            NutrientFoodCandidate.food_concept == food_concept,
        )
    )
    row = result.scalar_one_or_none()

    fields = dict(
        diet_tags=diet_tags,
        nutrient_per_100g=nutrient_per_100g,
        representative_sku_id=representative_sku_id,
        order_frequency=order_frequency,
        repurchase_rate=repurchase_rate,
        confidence=confidence,
        sample_size=sample_size,
    )
    from datetime import datetime, timezone
    fields["last_refreshed"] = datetime.now(timezone.utc)

    if row:
        for k, v in fields.items():
            setattr(row, k, v)
    else:
        row = NutrientFoodCandidate(id=str(uuid.uuid4()), nutrient=nutrient, food_concept=food_concept, **fields)
        db.add(row)


async def _rebuild_nutrient_food_map() -> dict:
    """
    Async body of rebuild_nutrient_food_map, extracted to a module-level
    coroutine so tests can `await` it directly against a real DB session
    (patching app.database.AsyncSessionLocal) — same pattern as
    app.tasks.maintenance._backfill_nutrition_concepts.

    Aggregation runs in Python, not SQL: nutrition_cache is a small, global,
    per-SKU table (thousands of rows, not millions), and the density values
    needed for a per-nutrient median span both first-class columns and a
    JSONB sub-dict, which is materially simpler to aggregate in Python than
    to express as a single cross-key-space SQL query — and it keeps the
    exact same NUTRIENT_KEYS-driven density lookup (_density_value) used
    everywhere else in this feature, rather than restating the key mapping
    as raw SQL.
    """
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone
    from statistics import median

    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.db import NutritionCache, OrderItem, Order, NutrientFoodCandidate
    from app.services.nutrition_resolution import _ALLOWED_NOTABLE_KEYS, _density_value, _row_to_dict
    from app.services.nutrient_candidates import SEED_FALLBACK_MIN_ROWS, SEED_FALLBACK_MIN_SAMPLE_SIZE

    async with AsyncSessionLocal() as db:
        # 1. Enriched nutrition_cache rows, grouped by food_concept. Exclude
        #    NULL ("not yet attempted") and "" (B1's convergence sentinel for
        #    "attempted, unresolvable") — both are concept-less.
        result = await db.execute(
            select(NutritionCache).where(
                NutritionCache.food_concept.isnot(None),
                NutritionCache.food_concept != "",
            )
        )
        cache_rows = result.scalars().all()

        concept_rows: dict[str, list[NutritionCache]] = defaultdict(list)
        sku_to_concept: dict[str, str] = {}
        for row in cache_rows:
            concept_rows[row.food_concept].append(row)
            sku_to_concept[row.sku_id] = row.food_concept

        # 2. order_items (last 90 days) joined to Order for placed_at, mapped
        #    to food_concept via sku_id -> concept lookup above.
        cutoff = datetime.now(timezone.utc) - timedelta(days=_ORDER_ITEMS_LOOKBACK_DAYS)
        oi_result = await db.execute(
            select(OrderItem.swiggy_sku_id, OrderItem.household_id)
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.placed_at >= cutoff)
        )
        oi_rows = oi_result.all()

        concept_order_freq: dict[str, int] = defaultdict(int)
        concept_household_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        concept_sku_freq: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for sku_id, household_id in oi_rows:
            concept = sku_to_concept.get(sku_id)
            if not concept:
                continue
            concept_order_freq[concept] += 1
            concept_household_counts[concept][household_id] += 1
            concept_sku_freq[concept][sku_id] += 1

        # 3+4. Explode by notable_nutrients, aggregate, upsert.
        upserted = 0
        for concept, rows in concept_rows.items():
            order_frequency = concept_order_freq.get(concept, 0)
            households = concept_household_counts.get(concept, {})
            repurchase_rate = (
                sum(1 for c in households.values() if c >= 2) / len(households)
                if households else None
            )
            sku_freq = concept_sku_freq.get(concept, {})
            # "in-stock" isn't available here — order_items records a past
            # purchase, not live stock status; B3 does a live search and
            # checks stock at request time. This is just "most-ordered SKU".
            representative_sku_id = max(sku_freq, key=sku_freq.get) if sku_freq else rows[0].sku_id

            diet_tags = _diet_tags_for_concept(concept)

            notable: set[str] = set()
            for r in rows:
                notable |= (set(r.notable_nutrients or []) & _ALLOWED_NOTABLE_KEYS)

            worst_row = min(rows, key=lambda r: _CONF_RANK.get(r.confidence, 0))
            worst_confidence = worst_row.confidence

            for nutrient in notable:
                values = [
                    v for r in rows
                    if (v := _density_value(_row_to_dict(r), nutrient)) is not None
                ]
                if not values:
                    continue
                await _upsert_candidate(
                    db, nutrient, concept, diet_tags,
                    nutrient_per_100g=median(values),
                    representative_sku_id=representative_sku_id,
                    order_frequency=order_frequency,
                    repurchase_rate=repurchase_rate,
                    confidence=worst_confidence,
                    sample_size=len(values),
                )
                upserted += 1

        await db.commit()

        # Observability: which (nutrient, diet) pairs are still below the
        # seed-fallback threshold after this run — B2's DoD requires this be
        # logged, not assumed, so seed-reliance shrinkage is visible over time.
        result = await db.execute(select(NutrientFoodCandidate))
        all_candidates = result.scalars().all()
        seed_fallback_pairs = []
        for nutrient in _ALLOWED_NOTABLE_KEYS:
            for diet in ("vegetarian", "vegan", "jain"):
                adequate = [
                    c for c in all_candidates
                    if c.nutrient == nutrient
                    and diet in (c.diet_tags or [])
                    and (c.sample_size or 0) >= SEED_FALLBACK_MIN_SAMPLE_SIZE
                ]
                if len(adequate) < SEED_FALLBACK_MIN_ROWS:
                    seed_fallback_pairs.append(f"{nutrient}:{diet}")

    logger.info(
        "nutrient_food_map_rebuilt",
        rows_upserted=upserted,
        rows_from_seed_fallback=len(seed_fallback_pairs),
        seed_fallback_pairs=seed_fallback_pairs,
    )
    return {"rows_upserted": upserted, "rows_from_seed_fallback": len(seed_fallback_pairs)}


@celery_app.task(queue="nutrition", name="app.tasks.nutrition.rebuild_nutrient_food_map")
def rebuild_nutrient_food_map():
    """Beat entry-point (nightly): see _rebuild_nutrient_food_map() for the
    implementation. Gap-to-Cart Phase B2."""
    try:
        return asyncio.run(_rebuild_nutrient_food_map())
    except Exception as e:
        logger.error("rebuild_nutrient_food_map_failed", error=str(e))
        raise
