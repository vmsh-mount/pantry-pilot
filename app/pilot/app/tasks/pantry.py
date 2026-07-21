from datetime import datetime, timezone

from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _item_to_dict(i) -> dict:
    if isinstance(i, dict):
        return {
            "name":     i.get("name", ""),
            "quantity": float(i.get("quantity", 1)),
            "unit":     i.get("unit") or "pcs",
            "category": i.get("category") or "grocery",
        }
    return {
        "name":     i.name,
        "quantity": float(i.quantity),
        "unit":     i.unit or "pcs",
        "category": i.category or "grocery",
    }


def _parse_placed_at(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    queue="pantry",
    name="app.tasks.pantry.update_pantry_post_order"
)
def update_pantry_post_order(self, household_id: str, order_id: str):
    """Update pantry stock from order_items already stored in our DB."""
    import asyncio
    from datetime import datetime, timezone
    from app.database import AsyncSessionLocal
    from app.models.db import Order, OrderItem
    from app.services.pantry_service import PantryService
    from sqlalchemy import select

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                order_result = await db.execute(
                    select(Order).where(Order.id == order_id)
                )
                order = order_result.scalar_one_or_none()
                if order and order.pantry_updated:
                    logger.info("pantry_update_already_done", household_id=household_id, order_id=order_id)
                    return

                result = await db.execute(
                    select(OrderItem).where(
                        OrderItem.order_id    == order_id,
                        OrderItem.household_id == household_id,
                    )
                )
                items = result.scalars().all()
                if not items:
                    logger.warning("pantry_update_no_items", household_id=household_id, order_id=order_id)
                    return
                order_items = [
                    {
                        "name":     item.product_name,
                        "quantity": float(item.quantity),
                        "unit":     item.unit,
                        "category": item.category or "grocery",
                    }
                    for item in items
                ]
                await PantryService(db).post_order_update(household_id, order_items)
                if order:
                    order.pantry_updated    = True
                    order.pantry_updated_at = datetime.now(timezone.utc)
                    await db.commit()
                logger.info("pantry_updated", household_id=household_id, order_id=order_id, item_count=len(items))
            except Exception as e:
                logger.error("pantry_update_failed", household_id=household_id, error=str(e))
                raise self.retry(exc=e)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    queue="pantry",
    name="app.tasks.pantry.sync_external_orders",
)
def sync_external_orders(self, household_id: str):
    """Sync external Swiggy orders (not placed by PP) into pantry_items."""
    import asyncio
    from datetime import timedelta

    async def _run():
        from app.database import AsyncSessionLocal
        from app.models.db import HouseholdPreferences, Order
        from app.services.auth_service import AuthService
        from app.services.pantry_service import PantryService
        from app.providers.factory import get_mcp_provider
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            try:
                access_token = await AuthService(db).get_valid_token(household_id)

                prefs_result = await db.execute(
                    select(HouseholdPreferences).where(
                        HouseholdPreferences.household_id == household_id
                    )
                )
                prefs = prefs_result.scalar_one_or_none()
                if not prefs:
                    logger.warning("external_sync_no_prefs", household_id=household_id)
                    return

                now = datetime.now(timezone.utc)
                since_ts = prefs.last_external_sync_at or (now - timedelta(days=30))
                if since_ts.tzinfo is None:
                    since_ts = since_ts.replace(tzinfo=timezone.utc)

                logger.info("external_sync_start", household_id=household_id, since_ts=since_ts.isoformat())

                mcp = get_mcp_provider(access_token)
                summaries = await mcp.get_orders(limit=20)
                in_window = [
                    s for s in summaries
                    if s.placed_at is not None and _parse_placed_at(s.placed_at) >= since_ts
                ]

                skip_result = await db.execute(
                    select(Order.swiggy_order_id).where(
                        Order.household_id == household_id,
                        Order.placed_at >= since_ts - timedelta(days=1),
                    )
                )
                skip_set = {row[0] for row in skip_result.all()}

                # Sort ascending so the watermark advances monotonically
                in_window_sorted = sorted(
                    in_window, key=lambda s: _parse_placed_at(s.placed_at)
                )

                processed = 0
                skipped = 0
                for summary in in_window_sorted:
                    if summary.order_id in skip_set:
                        logger.info("external_sync_skipped_pp_order", swiggy_order_id=summary.order_id)
                        skipped += 1
                        continue

                    order_detail = await mcp.get_order_details(summary.order_id)
                    raw_items = (
                        order_detail.get("items", [])
                        if isinstance(order_detail, dict)
                        else getattr(order_detail, "items", [])
                    )
                    order_items = [_item_to_dict(i) for i in raw_items]
                    if not order_items:
                        continue

                    await PantryService(db).post_order_update(household_id, order_items)

                    # Advance watermark after each success — retry resumes from here
                    prefs.last_external_sync_at = _parse_placed_at(summary.placed_at)
                    await db.commit()

                    logger.info("external_sync_processed", swiggy_order_id=summary.order_id, item_count=len(order_items))
                    processed += 1

                # Final watermark = now so next 4-hour window starts clean
                prefs.last_external_sync_at = now
                await db.commit()

                logger.info(
                    "external_sync_complete",
                    household_id=household_id,
                    orders_processed=processed,
                    orders_skipped=skipped,
                )
            except Exception as e:
                logger.error("external_sync_failed", household_id=household_id, error=str(e))
                raise self.retry(exc=e)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


@celery_app.task(
    queue="pantry",
    name="app.tasks.pantry.sync_external_orders_all",
)
def sync_external_orders_all():
    """Beat fan-out: dispatch sync_external_orders for every active household."""
    import asyncio

    async def _run():
        from app.database import AsyncSessionLocal
        from app.models.db import Household
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Household.id).where(
                    Household.onboarding_complete == True,
                    Household.is_active == True,
                )
            )
            household_ids = [row[0] for row in result.all()]

        for hh_id in household_ids:
            sync_external_orders.delay(str(hh_id))

        logger.info("external_sync_all_dispatched", count=len(household_ids))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()
