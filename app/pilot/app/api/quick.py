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
                                     (thin wrapper — see app/services/quick_checkout.py for
                                     the actual logic, which the AI assistant's checkout_basket
                                     tool also calls; tasks/features/ai-ordering-assistant.md §0)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import Address, ItemSignal
from app.schemas.common import APIResponse
from app.services import quick_basket as basket_svc
from app.services import quick_checkout
from app.services.basket_editing_service import BasketEditingService
from app.utils.exceptions import TokenExpiredError, SwiggyMCPError
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/quick", tags=["quick-order"])


# ── Auth helper ───────────────────────────────────────────────────────────────

def _household_id(request: Request) -> str | None:
    return request.session.get("household_id")


# _get_access_token / _resolve_swiggy_address moved to app/services/quick_checkout.py —
# they were checkout-only, and checkout itself now lives there too. See that
# module's docstring / tasks/features/ai-ordering-assistant.md Design §0.


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
    db: AsyncSession = Depends(get_db),
):
    """Thin wrapper — all logic lives in quick_checkout.checkout(), which the
    AI assistant's checkout_basket tool also calls (Design §0, avoids two
    copies of lock/cart-build/dry-run/MCP-call logic that must stay in sync)."""
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    result = await quick_checkout.checkout(household_id, db, body.swiggy_address_id)
    if not result["success"]:
        return APIResponse.fail(result["code"], result["message"])

    return APIResponse.ok({k: v for k, v in result.items() if k != "success"})


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
