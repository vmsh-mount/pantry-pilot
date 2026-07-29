"""
Quick Order API — adhoc Swiggy Instamart ordering with signal capture.

Endpoints (all under /v1/quick):
  GET    /search                  — search Swiggy products (no signal written)
  GET    /basket                  — return current Redis basket
  POST   /basket/add              — add item + write 'added' signal
  PATCH  /basket/item/{item_id}   — update qty/brand + write qty/brand signal
  DELETE /basket/item/{item_id}   — remove item + write 'removed' signal
  GET    /addresses               — list delivery addresses for picker
  POST   /checkout                — place order, write 'accepted' signals, update pantry + model
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import (
    Address, HouseholdPreferences, ItemSignal, Order, OrderItem,
)
from app.redis import get_redis
from app.schemas.common import APIResponse
from app.services import quick_basket as basket_svc
from app.services.basket_editing_service import BasketEditingService
from app.utils.exceptions import TokenExpiredError, SwiggyMCPError
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/quick", tags=["quick-order"])

_CART_LOCK_TIMEOUT    = 300  # seconds
_CART_LOCK_BLOCKING   = 10   # fail fast — don't make user wait


# ── Auth helper ───────────────────────────────────────────────────────────────

def _household_id(request: Request) -> str | None:
    return request.session.get("household_id")


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


# ── Signal helper ─────────────────────────────────────────────────────────────

async def _write_signal(
    db: AsyncSession,
    household_id: str,
    item_name: str,
    signal_type: str,
    previous_value: dict | None = None,
    new_value: dict | None = None,
) -> None:
    db.add(ItemSignal(
        household_id=household_id,
        loop_run_id=None,
        item_name=item_name,
        signal_type=signal_type,
        source="quick_order",
        previous_value=previous_value,
        new_value=new_value,
    ))
    await db.flush()


# ══════════════════════════════════════════════════════════════════════════════
# GET /search
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/search", response_model=APIResponse)
async def search_products(
    request: Request,
    q: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    try:
        svc = BasketEditingService()
        results = await svc.search_items(db, household_id, q, limit=limit)
    except TokenExpiredError:
        return APIResponse.fail("TOKEN_EXPIRED", "Swiggy session expired.")
    except SwiggyMCPError as e:
        return APIResponse.fail("SEARCH_ERROR", str(e))

    def _p(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    return APIResponse.ok({
        "query": q,
        "results": [
            {
                "sku_id":     _p(r, "sku_id"),
                "spin_id":    _p(r, "spin_id", "") or "",
                "item_name":  _p(r, "name") or _p(r, "item_name", ""),
                "brand":      _p(r, "brand"),
                "category":   _p(r, "category"),
                "unit":       _p(r, "unit", None) or _p(r, "quantity", None) or "units",
                "unit_price": float(_p(r, "price") or _p(r, "unit_price", 0)),
                "in_stock":   _p(r, "in_stock", True),
                "image_url":  _p(r, "image_url"),
            }
            for r in results
        ],
    })


# ══════════════════════════════════════════════════════════════════════════════
# GET /basket
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/basket", response_model=APIResponse)
async def get_basket(request: Request):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    items = await basket_svc.get_basket(household_id)
    for item in items:
        item.setdefault("in_stock", True)
    total = sum(i["unit_price"] * i["quantity"] for i in items)
    return APIResponse.ok({"items": items, "estimated_total": round(total, 2)})


# ══════════════════════════════════════════════════════════════════════════════
# POST /basket/add
# ══════════════════════════════════════════════════════════════════════════════

class AddItemRequest(BaseModel):
    item_name: str = Field(..., min_length=1)
    brand: Optional[str] = None
    sku_id: Optional[str] = None
    spin_id: Optional[str] = None
    category: Optional[str] = None
    unit: str = "units"
    quantity: int = 1
    unit_price: float = 0.0
    in_stock: bool = True


@router.post("/basket/add", response_model=APIResponse)
async def add_basket_item(
    request: Request,
    body: AddItemRequest,
    db: AsyncSession = Depends(get_db),
):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    entry = await basket_svc.add_item(household_id, body.model_dump())

    await _write_signal(
        db, household_id,
        item_name=entry["item_name"],
        signal_type="added",
        new_value={"quantity": entry["quantity"], "brand": entry["brand"]},
    )
    await db.commit()

    return APIResponse.ok({"item": entry})


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /basket/item/{item_id}
# ══════════════════════════════════════════════════════════════════════════════

class UpdateItemRequest(BaseModel):
    quantity: Optional[int] = None
    brand: Optional[str] = None


@router.patch("/basket/item/{item_id}", response_model=APIResponse)
async def update_basket_item(
    item_id: str,
    request: Request,
    body: UpdateItemRequest,
    db: AsyncSession = Depends(get_db),
):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    # Capture before-state for signal
    items_before = await basket_svc.get_basket(household_id)
    before = next((i for i in items_before if i["id"] == item_id), None)
    if not before:
        return APIResponse.fail("NOT_FOUND", "Item not in basket.")

    updated = await basket_svc.update_item(
        household_id, item_id,
        quantity=body.quantity,
        brand=body.brand,
    )

    # Guard before writing signals — item may have been removed from Redis
    # between the get_basket read above and the update_item call.
    if updated is None:
        return APIResponse.fail("NOT_FOUND", "Item no longer in basket.")

    # Determine signal type
    if body.brand is not None and body.brand != before.get("brand"):
        await _write_signal(
            db, household_id, item_name=before["item_name"],
            signal_type="brand_changed",
            previous_value={"brand": before.get("brand")},
            new_value={"brand": body.brand},
        )
    if body.quantity is not None:
        old_qty = before["quantity"]
        new_qty = max(1, body.quantity)
        if new_qty != old_qty:
            sig = "qty_increased" if new_qty > old_qty else "qty_decreased"
            await _write_signal(
                db, household_id, item_name=before["item_name"],
                signal_type=sig,
                previous_value={"quantity": old_qty},
                new_value={"quantity": new_qty},
            )

    await db.commit()
    return APIResponse.ok({"item": updated})


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /basket/item/{item_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.delete("/basket/item/{item_id}", response_model=APIResponse)
async def remove_basket_item(
    item_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    removed = await basket_svc.remove_item(household_id, item_id)
    if not removed:
        return APIResponse.fail("NOT_FOUND", "Item not in basket.")

    await _write_signal(
        db, household_id,
        item_name=removed["item_name"],
        signal_type="removed",
        previous_value={"quantity": removed["quantity"], "brand": removed.get("brand")},
    )
    await db.commit()
    return APIResponse.ok({"removed": True})


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /basket  — clear entire basket
# ══════════════════════════════════════════════════════════════════════════════

@router.delete("/basket", response_model=APIResponse)
async def clear_basket(request: Request):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    await basket_svc.clear_basket(household_id)
    return APIResponse.ok({"cleared": True})


# ══════════════════════════════════════════════════════════════════════════════
# GET /addresses
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/addresses", response_model=APIResponse)
async def list_addresses(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    result = await db.execute(
        select(Address).where(Address.household_id == household_id)
    )
    addresses = result.scalars().all()
    return APIResponse.ok({
        "addresses": [
            {
                "id":                a.id,
                "swiggy_address_id": a.swiggy_address_id,
                "label":             a.label,
                "is_default":        a.is_default,
            }
            for a in addresses
        ]
    })


# ══════════════════════════════════════════════════════════════════════════════
# POST /checkout
# ══════════════════════════════════════════════════════════════════════════════

class CheckoutRequest(BaseModel):
    swiggy_address_id: Optional[str] = None  # session-only override; does not persist


@router.post("/checkout", response_model=APIResponse)
async def checkout(
    request: Request,
    body: CheckoutRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    items = await basket_svc.get_basket(household_id)
    if not items:
        return APIResponse.fail("EMPTY_BASKET", "Basket is empty.")

    try:
        access_token = await _get_access_token(household_id, db)
    except TokenExpiredError:
        return APIResponse.fail("TOKEN_EXPIRED", "Swiggy session expired.")

    try:
        swiggy_address_id = await _resolve_swiggy_address(
            household_id, db, body.swiggy_address_id
        )
    except SwiggyMCPError as e:
        return APIResponse.fail("NO_ADDRESS", str(e))

    # Acquire cart lock — same key as Routines to prevent collision
    redis = await get_redis()
    lock_key = f"routine_cart_lock:{household_id}"
    lock = redis.lock(lock_key, timeout=_CART_LOCK_TIMEOUT, blocking_timeout=_CART_LOCK_BLOCKING)
    try:
        acquired = await lock.acquire(blocking=True)
    except Exception:
        acquired = False
    if not acquired:
        return APIResponse.fail("CART_LOCKED", "Another order is in progress. Please try again shortly.")

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
            return APIResponse.fail("NO_SKUS", "No SKU IDs in basket — cannot place order.")

        if get_settings().pantrypilot_dry_run:
            import uuid as _uuid
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
        return APIResponse.fail("CHECKOUT_ERROR", str(e))
    finally:
        try:
            await lock.release()
        except Exception:
            pass

    # Normalise order result
    def _o(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

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
    from app.services.order_events import dispatch_post_order_tasks
    update_pantry_post_order.delay(household_id, str(order.id))
    dispatch_post_order_tasks(order.id)

    background_tasks.add_task(_run_model_update, household_id)

    logger.info(
        "quick_order_placed",
        household_id=household_id,
        order_id=order.id,
        swiggy_order_id=swiggy_order_id,
        grand_total=grand_total,
    )
    return APIResponse.ok({
        "order_id":        order.id,
        "swiggy_order_id": swiggy_order_id,
        "item_total":      item_total,
        "delivery_fee":    delivery_fee,
        "taxes":           taxes,
        "grand_total":     grand_total,
        "items":           items,
    })


@router.get("/orders/recent", response_model=APIResponse)
async def recent_quick_orders(request: Request, db: AsyncSession = Depends(get_db)):
    """Return last 5 orders placed via Quick Order for the current household."""
    from sqlalchemy import desc, func
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    # Subquery: item count per order
    item_count_sq = (
        select(OrderItem.order_id, func.count().label("item_count"))
        .group_by(OrderItem.order_id)
        .subquery()
    )
    rows = await db.execute(
        select(Order, item_count_sq.c.item_count)
        .outerjoin(item_count_sq, Order.id == item_count_sq.c.order_id)
        .where(Order.household_id == household_id, Order.source == "quick_order")
        .order_by(desc(Order.placed_at))
        .limit(5)
    )
    return APIResponse.ok({
        "orders": [
            {
                "order_id":    str(o.id),
                "placed_at":   o.placed_at.isoformat(),
                "grand_total": float(o.grand_total or o.item_total or 0),
                "item_count":  int(count or 0),
            }
            for o, count in rows.all()
        ]
    })


@router.post("/orders/{order_id}/reorder", response_model=APIResponse)
async def reorder(request: Request, order_id: str, db: AsyncSession = Depends(get_db)):
    """Add all items from a past quick order back into the current basket."""
    from uuid import UUID
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    try:
        oid = UUID(order_id)
    except ValueError:
        return APIResponse.fail("INVALID_ORDER_ID", "Invalid order ID.")

    order_result = await db.execute(
        select(Order).where(Order.id == oid, Order.household_id == household_id)
    )
    order = order_result.scalar_one_or_none()
    if not order:
        return APIResponse.fail("NOT_FOUND", "Order not found.")

    items_result = await db.execute(
        select(OrderItem).where(OrderItem.order_id == oid)
    )
    order_items = items_result.scalars().all()

    existing_skus = {i["sku_id"] for i in await basket_svc.get_basket(household_id) if i.get("sku_id")}

    added = []
    for oi in order_items:
        if oi.swiggy_sku_id and oi.swiggy_sku_id in existing_skus:
            continue
        entry = await basket_svc.add_item(household_id, {
            "item_name":  oi.product_name,
            "brand":      oi.brand,
            "sku_id":     oi.swiggy_sku_id,
            "unit":       oi.unit,
            "quantity":   oi.quantity,
            "unit_price": float(oi.unit_price or 0),
            "in_stock":   True,
        })
        added.append(entry)

    return APIResponse.ok({"added": len(added), "items": added})


async def _run_model_update(household_id: str) -> None:
    from app.database import AsyncSessionLocal
    from app.services.household_model_service import update_model
    try:
        async with AsyncSessionLocal() as db:
            await update_model(household_id, loop_run_id=None, db=db)
            await db.commit()
    except Exception as e:
        logger.warning("quick_order_model_update_failed", household_id=household_id, error=str(e))
