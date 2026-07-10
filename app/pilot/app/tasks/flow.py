"""Flow — periodic signal evaluation task."""
import asyncio
from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(
    name="app.tasks.flow.evaluate_flow_signals",
    bind=True,
    max_retries=3,
    queue="planning",
)
def evaluate_flow_signals(self):
    """
    Runs every 4 hours. For each active, non-paused household:
    1. Check if any anchor items are estimated to deplete within N days
    2. Check days elapsed vs household's typical reorder interval
    3. If signal fires, enqueue trigger_planning_loop for that household
    """

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_evaluate_all_households())
    except Exception as e:
        logger.error("evaluate_flow_signals_failed", error=str(e))
        raise self.retry(exc=e)
    finally:
        loop.close()


async def _evaluate_all_households():
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models.db import Household
    from app.database import AsyncSessionLocal

    UTC = timezone.utc
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Household).where(
                Household.is_active == True,
                Household.is_paused == False,
                Household.onboarding_complete == True,
            )
        )
        households = result.scalars().all()

        for household in households:
            try:
                await _evaluate_household(household, db, now)
            except Exception as e:
                logger.error(
                    "flow_signal_eval_failed",
                    household_id=str(household.id),
                    error=str(e),
                )


async def _evaluate_household(household, db, now):
    from datetime import timezone
    from sqlalchemy import select
    from app.models.db import HouseholdPreferences, HouseholdModel, PantryItem, FlowBasket
    from app.tasks.planning import trigger_planning_loop

    UTC = timezone.utc

    # Skip if a Flow basket is already held (not yet delivered/confirmed)
    held = await db.execute(
        select(FlowBasket).where(
            FlowBasket.household_id == household.id,
            FlowBasket.status == "held",
        )
    )
    if held.scalars().first():
        return  # basket already in flight

    # Get household preferences for last_run_at
    prefs_result = await db.execute(
        select(HouseholdPreferences).where(HouseholdPreferences.household_id == household.id)
    )
    prefs = prefs_result.scalars().first()

    # Get household model for reorder_horizon_days and anchors
    model_result = await db.execute(
        select(HouseholdModel).where(HouseholdModel.household_id == household.id)
    )
    model = model_result.scalars().first()

    horizon_days = (model.reorder_horizon_days if model and model.reorder_horizon_days else 7)
    buffer_days = 2  # fire early to account for delivery lead time

    # Signal 1: elapsed time since last order
    last_run_at = prefs.last_run_at if prefs else None
    if last_run_at:
        if last_run_at.tzinfo is None:
            last_run_at = last_run_at.replace(tzinfo=UTC)
        days_since_last = (now - last_run_at).days
        if days_since_last >= horizon_days - buffer_days:
            logger.info(
                "flow_signal_elapsed_time",
                household_id=str(household.id),
                days_since=days_since_last,
            )
            trigger_planning_loop.delay(str(household.id), trigger_type="flow_elapsed")
            return

    # Signal 2: anchor items estimated to deplete within horizon + buffer
    pantry_result = await db.execute(
        select(PantryItem).where(PantryItem.household_id == household.id)
    )
    pantry_items = pantry_result.scalars().all()

    anchors = set()
    if model and model.anchors:
        anchors = {
            a["item_name"].lower()
            for a in model.anchors
            if a.get("status") in ("confirmed", "provisional")
        }

    for item in pantry_items:
        if item.item_name.lower() not in anchors:
            continue
        if not item.avg_weekly_consumption or item.avg_weekly_consumption <= 0:
            continue
        daily_velocity = item.avg_weekly_consumption / 7
        qty_remaining = item.estimated_qty_remaining or 0
        days_remaining = qty_remaining / daily_velocity if daily_velocity > 0 else 999
        if days_remaining <= horizon_days + buffer_days:
            logger.info(
                "flow_signal_anchor_depleting",
                household_id=str(household.id),
                item=item.item_name,
                days_remaining=round(days_remaining, 1),
            )
            trigger_planning_loop.delay(str(household.id), trigger_type="flow_anchor_depleting")
            return
