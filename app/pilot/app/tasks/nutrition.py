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
