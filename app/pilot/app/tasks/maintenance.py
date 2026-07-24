from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(queue="maintenance", name="app.tasks.maintenance.check_token_expiry")
def check_token_expiry():
    """Runs daily at 9 AM IST. Sends re-auth reminders for expiring tokens."""
    import asyncio
    from app.database import AsyncSessionLocal
    from app.services.auth_service import AuthService
    from app.services.whatsapp_service import WhatsAppService

    async def _run():
        async with AsyncSessionLocal() as db:
            from datetime import datetime, timezone
            from sqlalchemy import select as sa_select
            from app.models.db import Household

            expiring = await AuthService(db).get_expiring_tokens()
            wa       = WhatsAppService()

            for token_record in expiring:
                hours_remaining = (
                    token_record.token_expiry - datetime.now(timezone.utc)
                ).total_seconds() / 3600

                # Fetch phone number for this household
                hh_result = await db.execute(
                    sa_select(Household).where(Household.id == token_record.household_id)
                )
                household = hh_result.scalar_one_or_none()
                phone = household.whatsapp_number if household else None
                if not phone:
                    continue

                reauth_url = f"https://pantrypilot.in/reauth?hid={token_record.household_id}"
                expiry_label = token_record.token_expiry.strftime("%A, %d %b at %I %p")

                if hours_remaining <= 24 and not token_record.nudge_24hr_sent:
                    await wa.send_reauth_24hr(phone, expiry_label, reauth_url)
                    await AuthService(db).mark_nudge_sent(token_record.household_id, "24hr")
                    logger.info("reauth_nudge_sent", household_id=token_record.household_id, urgency="24hr")

                elif hours_remaining <= 48 and not token_record.nudge_48hr_sent:
                    await wa.send_reauth_48hr(phone, expiry_label, reauth_url)
                    await AuthService(db).mark_nudge_sent(token_record.household_id, "48hr")
                    logger.info("reauth_nudge_sent", household_id=token_record.household_id, urgency="48hr")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


@celery_app.task(queue="maintenance", name="app.tasks.maintenance.catchup_missed_runs")
def catchup_missed_runs():
    """Runs every hour. Catches planning loops that were missed (e.g. system downtime)."""
    import asyncio
    from datetime import datetime, timezone, timedelta
    from app.database import AsyncSessionLocal
    from app.services.planning_service import PlanningService
    from app.tasks.planning import trigger_planning_loop

    async def _run():
        async with AsyncSessionLocal() as db:
            from app.services.household_service import HouseholdService
            household_svc = HouseholdService(db)
            missed = await household_svc.get_missed_runs(
                before=datetime.now(timezone.utc) - timedelta(minutes=30)
            )
            for household_id in missed:
                trigger_planning_loop.delay(household_id)
                logger.info("catchup_run_triggered", household_id=household_id)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


async def _backfill_nutrition_concepts(batch_size: int = 200) -> dict:
    """
    Async body of backfill_nutrition_concepts, extracted to a module-level
    coroutine so tests can `await` it directly against a real DB session
    (patching app.database.AsyncSessionLocal) without fighting the Celery
    wrapper's own event-loop management — same pattern as
    app.tasks.routines._execute.

    Mechanical-only — no Haiku calls. Every row already has its own per-100g
    values stored (regardless of original source), so notable_nutrients is
    derived from those directly. food_concept is derived from whichever name
    is available:
      - matched_name, if the row came from OFF/USDA;
      - otherwise a representative OrderItem.product_name for the same
        swiggy_sku_id (LLM-sourced rows never had a name column of their own —
        matched_name is None per _estimate_llm's return shape).

    Convergence: the query selects `food_concept IS NULL` as "not yet
    attempted." A row that's genuinely unresolvable — no name recoverable, or
    the mechanical normalizer legitimately returns None (e.g. an all-stopword
    name) — is stamped food_concept = "" (empty string), NOT left NULL. NULL
    means "never tried"; "" means "tried, gave up." Without this distinction
    the same unresolvable rows would be re-selected by every batch forever,
    since NULL-selecting them repeatedly never shrinks the candidate set —
    the "more" field this task returns exists so a caller can loop until
    done, and that loop would spin forever on the residual tail otherwise.
    B2 must skip both NULL and "" when aggregating by food_concept.
    """
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.db import NutritionCache, OrderItem
    from app.services.nutrition_resolution import (
        _mechanical_food_concept, _mechanical_notable_nutrients,
    )

    updated = 0
    gave_up = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(NutritionCache)
            .where(NutritionCache.food_concept.is_(None))
            .limit(batch_size)
        )
        rows = result.scalars().all()

        for row in rows:
            name = row.matched_name
            brand = None

            if not name:
                # LLM-sourced row: recover a representative name via order_items.
                oi_result = await db.execute(
                    select(OrderItem)
                    .where(OrderItem.swiggy_sku_id == row.sku_id)
                    .limit(1)
                )
                oi = oi_result.scalar_one_or_none()
                if oi:
                    name = oi.product_name
                    brand = oi.brand

            if not name:
                # No name recoverable at all — stamp "" (tried, gave up),
                # NOT NULL, so this row leaves the IS NULL candidate set.
                row.food_concept = ""
                gave_up += 1
                continue

            resolved_view = {
                "protein_per_100g": row.protein_per_100g,
                "fiber_per_100g": row.fiber_per_100g,
                "sodium_mg_per_100g": row.sodium_mg_per_100g,
                "nutrients": row.nutrients or {},
            }
            concept = _mechanical_food_concept(name, brand)
            if concept is None:
                # Normalizer legitimately found nothing (e.g. all-stopword
                # name) — same "tried, gave up" sentinel, same reason.
                row.food_concept = ""
                gave_up += 1
            else:
                row.food_concept = concept
                updated += 1
            row.notable_nutrients = _mechanical_notable_nutrients(resolved_view)

        await db.commit()

    logger.info(
        "nutrition_concept_backfill_batch_done",
        updated=updated, gave_up=gave_up, batch_size=batch_size,
    )
    return {"updated": updated, "gave_up": gave_up, "more": len(rows) == batch_size}


@celery_app.task(queue="maintenance", name="app.tasks.maintenance.backfill_nutrition_concepts")
def backfill_nutrition_concepts(batch_size: int = 200):
    """
    One-time backfill (Gap-to-Cart Phase B1). Run manually once, not on a
    beat schedule:

        docker compose exec pilot python3 -c \
            "from app.tasks.maintenance import backfill_nutrition_concepts; \
             backfill_nutrition_concepts.delay()"

    See _backfill_nutrition_concepts() for the implementation and the
    convergence contract (NULL vs "" sentinel).
    """
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_backfill_nutrition_concepts(batch_size))
    finally:
        loop.close()
