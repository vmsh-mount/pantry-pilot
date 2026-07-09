"""
Celery tasks for Routines:
  - execute_routine_run   : places a single routine order
  - check_due_routines    : Beat task (every 15 min) that enqueues due runs + logs missed ones
"""
import json
import asyncio
from datetime import datetime, timedelta, timezone

from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

logger = get_logger(__name__)
UTC = timezone.utc

# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── execute_routine_run ───────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.routines.execute_routine_run", queue="planning")
def execute_routine_run(routine_id: str):
    _run_async(_execute(routine_id))


async def _execute(routine_id: str):
    from app.database import AsyncSessionLocal
    from app.models.db import Routine, RoutineItem, RoutineRun
    from app.services.routines_service import compute_next_run_at
    from app.services.auth_service import AuthService
    from app.mcp.swiggy import SwiggyMCPClient
    from app.redis import get_redis
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Routine)
            .options(selectinload(Routine.items), selectinload(Routine.household))
            .where(Routine.id == routine_id)
        )
        routine = result.scalar_one_or_none()
        if not routine or routine.status != "active":
            logger.info("routine_run_skipped_not_active", routine_id=routine_id)
            return

        scheduled_at = routine.next_run_at or datetime.now(UTC)

        # Step 2: household paused?
        if routine.household.is_paused:
            db.add(RoutineRun(
                routine_id=routine_id,
                scheduled_at=scheduled_at,
                status="skipped",
                skip_reason="household_paused",
            ))
            routine.next_run_at = compute_next_run_at(routine)
            _check_ended(routine)
            await db.commit()
            logger.info("routine_run_household_paused", routine_id=routine_id)
            return

        # Step 3: Redis cart lock
        redis = await get_redis()
        lock_key = f"routine_cart_lock:{routine.household_id}"
        lock = redis.lock(lock_key, timeout=300, blocking_timeout=30)

        try:
            acquired = await lock.acquire(blocking=True)
        except Exception:
            acquired = False
        if not acquired:
            db.add(RoutineRun(
                routine_id=routine_id,
                scheduled_at=scheduled_at,
                status="failed",
                skip_reason="lock_timeout",
            ))
            routine.next_run_at = compute_next_run_at(routine)
            _check_ended(routine)
            await db.commit()
            logger.warning("routine_run_lock_timeout", routine_id=routine_id)
            return

        try:
            # Step 4: token validity
            token = None
            try:
                token = await AuthService(db).get_valid_token(routine.household_id)
            except Exception:
                pass

            if not token:
                db.add(RoutineRun(
                    routine_id=routine_id,
                    scheduled_at=scheduled_at,
                    status="failed",
                    skip_reason="token_expired",
                ))
                routine.next_run_at = compute_next_run_at(routine)
                _check_ended(routine)
                await db.commit()
                await _notify_wa(db, routine, "token_expired", [], 0)
                logger.warning("routine_run_token_expired", routine_id=routine_id)
                return

            mcp = SwiggyMCPClient(token)

            # Resolve swiggy address ID once for cart + checkout
            from app.models.db import HouseholdPreferences, Address
            from sqlalchemy import select as sa_select
            prefs = await db.scalar(sa_select(HouseholdPreferences).where(HouseholdPreferences.household_id == routine.household_id))
            addr = await db.get(Address, prefs.preferred_address_id) if prefs and prefs.preferred_address_id else None
            swiggy_addr_id = addr.swiggy_address_id if addr else None

            # Step 5: clear cart
            try:
                await mcp.clear_cart()
            except Exception as e:
                logger.warning("routine_clear_cart_failed", routine_id=routine_id, error=str(e))

            # Step 6: resolve items
            available_items = []   # list of {"sku_id": ..., "quantity": ...}
            skipped_items = []

            for item in routine.items:
                product_id = item.swiggy_product_id
                resolved = False

                if product_id:
                    available_items.append({"sku_id": product_id, "quantity": int(item.quantity)})
                    resolved = True

                if not resolved and swiggy_addr_id:
                    try:
                        search_result = await mcp.search_products(item.item_name, swiggy_addr_id)
                        products = search_result.products if search_result else []
                        if products:
                            new_pid = getattr(products[0], "sku_id", None)
                            if new_pid:
                                item.swiggy_product_id = new_pid
                                item.swiggy_product_name = getattr(products[0], "name", item.item_name)
                                available_items.append({"sku_id": new_pid, "quantity": int(item.quantity)})
                                resolved = True
                    except Exception as e:
                        logger.warning("routine_item_search_failed", item=item.item_name, error=str(e))

                if not resolved:
                    skipped_items.append({"item_name": item.item_name, "reason": "unavailable"})

            # Step 7: all items unavailable?
            if not available_items:
                db.add(RoutineRun(
                    routine_id=routine_id,
                    scheduled_at=scheduled_at,
                    status="failed",
                    skip_reason="all_items_unavailable",
                    skipped_items=json.dumps(skipped_items),
                ))
                routine.next_run_at = compute_next_run_at(routine)
                _check_ended(routine)
                await db.commit()
                await _notify_wa(db, routine, "all_items_unavailable", skipped_items, 0)
                return

            # Step 8: push cart
            try:
                await mcp.update_cart(available_items, address_id=swiggy_addr_id)
            except Exception as e:
                logger.warning("routine_update_cart_failed", routine_id=routine_id, error=str(e))

            # Step 9: checkout
            total_amount = 0.0
            order_id = None
            try:
                checkout_result = await mcp.checkout(address_id=swiggy_addr_id or "")
                total_amount = float(getattr(checkout_result, "grand_total", 0) or 0)
                # Create order record
                from app.models.db import Order, OrderItem
                order = Order(
                    household_id=routine.household_id,
                    swiggy_order_id=_extract_order_id(checkout_result) or f"routine_{routine_id}",
                    status="confirmed",
                    grand_total=total_amount,
                )
                db.add(order)
                await db.flush()
                order_id = order.id
            except Exception as e:
                logger.error("routine_checkout_failed", routine_id=routine_id, error=str(e))
                db.add(RoutineRun(
                    routine_id=routine_id,
                    scheduled_at=scheduled_at,
                    status="failed",
                    skip_reason="checkout_failed",
                    skipped_items=json.dumps(skipped_items),
                ))
                routine.next_run_at = compute_next_run_at(routine)
                _check_ended(routine)
                await db.commit()
                return

            # Step 10: record routine run
            status = "partial" if skipped_items else "placed"
            db.add(RoutineRun(
                routine_id=routine_id,
                scheduled_at=scheduled_at,
                status=status,
                order_id=order_id,
                skipped_items=json.dumps(skipped_items) if skipped_items else None,
                placed_at=datetime.now(UTC),
                total_amount=total_amount,
            ))

            # Step 13: advance next_run_at
            routine.next_run_at = compute_next_run_at(routine)
            _check_ended(routine)
            await db.commit()

            # Step 12: WhatsApp notification
            await _notify_wa(db, routine, status, skipped_items, total_amount)
            logger.info("routine_run_completed", routine_id=routine_id, status=status, total=total_amount)

        finally:
            try:
                await lock.release()
            except Exception:
                pass


def _check_ended(routine):
    """Set routine to ended if next_run_at is past end_date."""
    if not routine.end_date:
        return
    end = routine.end_date if routine.end_date.tzinfo else routine.end_date.replace(tzinfo=UTC)
    if not routine.next_run_at or routine.next_run_at > end:
        routine.status = "ended"


def _extract_pid(obj):
    if isinstance(obj, dict):
        return obj.get("id") or obj.get("product_id") or obj.get("sku_id")
    return getattr(obj, "id", None) or getattr(obj, "product_id", None)


def _extract_name(obj):
    if isinstance(obj, dict):
        return obj.get("name") or obj.get("product_name", "")
    return getattr(obj, "name", "") or getattr(obj, "product_name", "")


def _extract_order_id(obj):
    if isinstance(obj, dict):
        return obj.get("order_id") or obj.get("id")
    return getattr(obj, "order_id", None) or getattr(obj, "id", None)


async def _notify_wa(db, routine, status: str, skipped_items: list, total: float):
    """Send WhatsApp notification after a routine run."""
    try:
        from app.models.db import Household
        from sqlalchemy import select
        result = await db.execute(select(Household).where(Household.id == routine.household_id))
        household = result.scalar_one_or_none()
        if not household or not household.whatsapp_number:
            return
        from app.config import get_settings
        if not get_settings().whatsapp_enabled:
            logger.info("routine_wa_skipped_disabled", routine_id=routine.id)
            return

        from app.providers.factory import get_whatsapp_provider
        wa = get_whatsapp_provider()
        name = routine.name
        skipped_names = ", ".join(i["item_name"] for i in skipped_items) if skipped_items else ""

        if status == "placed":
            msg = f"Your {name} order was placed — ₹{total:.0f}. On its way!"
        elif status == "partial":
            msg = f"Your {name} order was placed — ₹{total:.0f}. Not available this time: {skipped_names}."
        elif status == "all_items_unavailable":
            msg = f"Your {name} order couldn't be placed — all items were out of stock. Check the app to adjust."
        elif status == "token_expired":
            msg = f"Your {name} order couldn't be placed — your Swiggy session expired. Open the app to reconnect."
        else:
            return

        await wa.send_text(household.whatsapp_number, msg)
    except Exception as e:
        logger.warning("routine_wa_notify_failed", routine_id=routine.id, error=str(e))


# ── check_due_routines ────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.routines.check_due_routines", queue="planning")
def check_due_routines():
    _run_async(_check_due())


async def _check_due():
    from app.database import AsyncSessionLocal
    from app.models.db import Routine, RoutineRun
    from app.services.routines_service import compute_next_run_at
    from sqlalchemy import select

    now = datetime.now(UTC)

    async with AsyncSessionLocal() as db:
        # 1. Missed runs: next_run_at is in the past by >15min
        missed_result = await db.execute(
            select(Routine).where(
                Routine.status == "active",
                Routine.next_run_at < now - timedelta(minutes=15),
            )
        )
        missed = missed_result.scalars().all()
        for r in missed:
            db.add(RoutineRun(
                routine_id=r.id,
                scheduled_at=r.next_run_at,
                status="skipped",
                skip_reason="missed",
            ))
            r.next_run_at = compute_next_run_at(r)
            _check_ended(r)
            logger.info("routine_run_missed", routine_id=r.id)

        if missed:
            await db.commit()

        # 2. Due runs: fire within the next 15-minute window
        due_result = await db.execute(
            select(Routine).where(
                Routine.status == "active",
                Routine.next_run_at >= now - timedelta(minutes=15),
                Routine.next_run_at <= now + timedelta(minutes=15),
            )
        )
        due = due_result.scalars().all()
        for r in due:
            eta = r.next_run_at
            # Advance next_run_at now so the next Beat tick doesn't re-enqueue or mis-classify as missed
            r.next_run_at = compute_next_run_at(r, after=r.next_run_at)
            _check_ended(r)
            execute_routine_run.apply_async(args=[r.id], eta=eta)
            logger.info("routine_run_enqueued", routine_id=r.id, eta=str(eta))

        if due:
            await db.commit()
