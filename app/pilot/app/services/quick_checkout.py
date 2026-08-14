"""
Quick Order checkout — cart lock, Swiggy MCP checkout call, Order/OrderItem
persistence, and post-order signal/background dispatch.

Extracted from api/quick.py's POST /checkout route body so callers other
than that one REST route (specifically: the AI ordering assistant's
checkout_basket tool) can invoke the exact same logic instead of
duplicating it. See tasks/features/ai-ordering-assistant.md, Design §0 —
this file existing at all is that PRD's prerequisite, not incidental
refactoring.

The REST route in api/quick.py should now be a thin wrapper: resolve
household_id from the session, call checkout() below, translate the result
dict into an APIResponse. Response codes and DB writes are unchanged from
before this extraction. One dispatch mechanism did change: the household-
model-update trigger moved from FastAPI's BackgroundTasks.add_task() (only
available inside a route, bound to a Request) to a Celery task — see
app/tasks/household_model.py's docstring for why. Everything else this
function does (the cart lock, the two other post-checkout dispatches) is
unchanged.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Address, HouseholdPreferences, ItemSignal, Order, OrderItem
from app.redis import get_redis
from app.services import quick_basket as basket_svc
from app.utils.exceptions import SwiggyMCPError, TokenExpiredError
from app.utils.logging import get_logger

logger = get_logger(__name__)

_CART_LOCK_TIMEOUT  = 300  # seconds
_CART_LOCK_BLOCKING = 10   # fail fast — don't make caller wait


async def _get_access_token(household_id: str, db: AsyncSession) -> str:
    from app.services.auth_service import AuthService
    return await AuthService(db).get_valid_token(household_id)


async def _resolve_swiggy_address(
    household_id: str,
    db: AsyncSession,
    override_swiggy_address_id: str | None = None,
) -> str:
    """Return the Swiggy address ID to use for this order."""
    if override_swiggy_address_id:
        return override_swiggy_address_id

    prefs_result = await db.execute(
        select(HouseholdPreferences).where(HouseholdPreferences.household_id == household_id)
    )
    prefs = prefs_result.scalar_one_or_none()
    if prefs and prefs.preferred_address_id:
        addr_result = await db.execute(
            select(Address).where(Address.id == prefs.preferred_address_id)
        )
        addr = addr_result.scalar_one_or_none()
        if addr:
            return addr.swiggy_address_id

    raise SwiggyMCPError("No delivery address configured.")


def _o(obj, key, default=None):
    """Read a field off either a dict or an object (MCPCheckoutResult may be either
    depending on provider)."""
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


async def checkout(
    household_id: str,
    db: AsyncSession,
    swiggy_address_id_override: str | None = None,
) -> dict[str, Any]:
    """
    Place the household's current Quick Order basket.

    Returns a result dict, never raises for expected failure modes — same
    shape either way so callers (the REST route, the assistant's tool
    handler) don't need their own try/except CheckoutError plumbing:

      Success: {"success": True, "order_id":, "swiggy_order_id":,
                "item_total":, "delivery_fee":, "taxes":, "grand_total":,
                "items":}
      Failure: {"success": False, "code":, "message":}
              code is one of the same strings the REST route always
              returned via APIResponse.fail: EMPTY_BASKET, TOKEN_EXPIRED,
              NO_ADDRESS, CART_LOCKED, NO_SKUS, CHECKOUT_ERROR.

    Re-fetches the basket itself (via basket_svc.get_basket) rather than
    accepting a caller-supplied item list — callers that re-validate before
    checkout (see the assistant's confirm-time re-validation, Design §0)
    should re-fetch and compare *before* calling this, not pass items in;
    this function is always the single source of truth for what actually
    gets checked out.
    """
    items = await basket_svc.get_basket(household_id)
    if not items:
        return {"success": False, "code": "EMPTY_BASKET", "message": "Basket is empty."}

    try:
        access_token = await _get_access_token(household_id, db)
    except TokenExpiredError:
        return {"success": False, "code": "TOKEN_EXPIRED", "message": "Swiggy session expired."}

    try:
        swiggy_address_id = await _resolve_swiggy_address(
            household_id, db, swiggy_address_id_override
        )
    except SwiggyMCPError as e:
        return {"success": False, "code": "NO_ADDRESS", "message": str(e)}

    # Acquire cart lock — same key as Routines to prevent collision
    redis = await get_redis()
    lock_key = f"routine_cart_lock:{household_id}"
    lock = redis.lock(lock_key, timeout=_CART_LOCK_TIMEOUT, blocking_timeout=_CART_LOCK_BLOCKING)
    try:
        acquired = await lock.acquire(blocking=True)
    except Exception:
        acquired = False
    if not acquired:
        return {"success": False, "code": "CART_LOCKED", "message": "Another order is in progress. Please try again shortly."}

    try:
        from app.config import get_settings
        from app.providers.factory import get_mcp_provider

        # Build cart payload — both spinId and skuId required by Swiggy MCP
        cart_items = [
            {"sku_id": i["sku_id"], "spin_id": i.get("spin_id") or "", "quantity": i["quantity"]}
            for i in items
            if i.get("sku_id")
        ]
        if not cart_items:
            return {"success": False, "code": "NO_SKUS", "message": "No SKU IDs in basket — cannot place order."}

        if get_settings().pantrypilot_dry_run:
            fake_id = f"dry_run_{_uuid.uuid4().hex[:12]}"
            logger.info("quick_order_dry_run", household_id=household_id, fake_order_id=fake_id)
            from app.mcp.types import MCPCheckoutResult
            order_result = MCPCheckoutResult(
                order_id=fake_id, status="PLACED", grand_total=float(sum(i["unit_price"] * i["quantity"] for i in items)),
                estimated_delivery="Dry run — no order placed",
            )
        else:
            client = get_mcp_provider(access_token)
            await client.clear_cart()
            await client.update_cart(cart_items, swiggy_address_id)
            order_result = await client.checkout(swiggy_address_id)

    except SwiggyMCPError as e:
        logger.error("quick_order_checkout_failed", household_id=household_id, error=str(e))
        return {"success": False, "code": "CHECKOUT_ERROR", "message": str(e)}
    finally:
        try:
            await lock.release()
        except Exception:
            pass

    swiggy_order_id = _o(order_result, "order_id") or _o(order_result, "swiggy_order_id", "")
    item_total   = float(sum(i["unit_price"] * i["quantity"] for i in items))
    delivery_fee = float(_o(order_result, "delivery_fee", 0))
    taxes        = float(_o(order_result, "taxes", 0))
    # Prefer Swiggy's confirmed grand_total (captures promos/discounts applied at checkout).
    # Fall back to local sum only when the checkout response omits it.
    _gt = _o(order_result, "grand_total", None)
    grand_total  = float(_gt) if _gt is not None else (item_total + delivery_fee + taxes)

    now = datetime.now(timezone.utc)

    # Persist Order
    order = Order(
        household_id=household_id,
        loop_run_id=None,
        swiggy_order_id=swiggy_order_id,
        swiggy_address_id=swiggy_address_id,
        item_total=item_total,
        delivery_fee=delivery_fee,
        taxes=taxes,
        grand_total=grand_total,
        status="placed",
        source="quick_order",
        placed_at=now,
    )
    db.add(order)
    await db.flush()  # get order.id

    for i in items:
        db.add(OrderItem(
            order_id=order.id,
            household_id=household_id,
            swiggy_sku_id=i.get("sku_id", ""),
            product_name=i["item_name"],
            brand=i.get("brand"),
            category=i.get("category"),
            quantity=i["quantity"],
            unit=i.get("unit", "units"),
            unit_price=i["unit_price"],
            total_price=i["unit_price"] * i["quantity"],
        ))

    # Write accepted signals for all checkout items
    for i in items:
        db.add(ItemSignal(
            household_id=household_id,
            loop_run_id=None,
            item_name=i["item_name"],
            signal_type="accepted",
            source="quick_order",
            new_value={"quantity": i["quantity"], "brand": i.get("brand"), "sku_id": i.get("sku_id")},
        ))

    await db.commit()

    # Clear basket now that order is placed
    await basket_svc.clear_basket(household_id)

    # Post-order async tasks
    from app.tasks.pantry import update_pantry_post_order
    from app.tasks.household_model import update_household_model
    from app.services.order_events import dispatch_post_order_tasks
    update_pantry_post_order.delay(household_id, str(order.id))
    dispatch_post_order_tasks(order.id)
    update_household_model.delay(household_id)

    logger.info(
        "quick_order_placed",
        household_id=household_id,
        order_id=order.id,
        swiggy_order_id=swiggy_order_id,
        grand_total=grand_total,
    )
    return {
        "success":         True,
        "order_id":        order.id,
        "swiggy_order_id": swiggy_order_id,
        "item_total":      item_total,
        "delivery_fee":    delivery_fee,
        "taxes":           taxes,
        "grand_total":     grand_total,
        "items":           items,
    }
