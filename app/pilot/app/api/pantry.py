from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import PantryItem
from app.schemas.common import APIResponse
from app.schemas.pantry import PantryItemUpdate

router = APIRouter(prefix="/pantry", tags=["pantry"])


def _household_id(request: Request) -> str | None:
    return request.session.get("household_id")


def _status(item: PantryItem) -> str:
    qty = float(item.estimated_qty_remaining)
    if qty <= 0:
        return "depleted"
    if qty <= float(item.reorder_threshold):
        return "low"
    return "stocked"


def _serialize(item: PantryItem) -> dict:
    return {
        "id": item.id,
        "item_name": item.item_name,
        "category": item.category,
        "standard_unit": item.standard_unit,
        "estimated_qty_remaining": float(item.estimated_qty_remaining),
        "reorder_threshold": float(item.reorder_threshold),
        "avg_weekly_consumption": float(item.avg_weekly_consumption) if item.avg_weekly_consumption is not None else None,
        "last_ordered_qty": float(item.last_ordered_qty) if item.last_ordered_qty is not None else None,
        "last_ordered_at": item.last_ordered_at.isoformat() if item.last_ordered_at else None,
        "times_ordered": item.times_ordered,
        "status": _status(item),
    }


@router.get("", response_model=APIResponse)
async def list_pantry(request: Request, db: AsyncSession = Depends(get_db)):
    hid = _household_id(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    result = await db.execute(
        select(PantryItem)
        .where(PantryItem.household_id == hid, PantryItem.is_active == True)
        .order_by(PantryItem.item_name)
    )
    items = result.scalars().all()
    serialized = [_serialize(i) for i in items]

    counts = {
        "total": len(serialized),
        "low": sum(1 for i in serialized if i["status"] == "low"),
        "depleted": sum(1 for i in serialized if i["status"] == "depleted"),
    }
    return APIResponse.ok({"items": serialized, "counts": counts})


@router.patch("/{item_id}", response_model=APIResponse)
async def update_pantry_item(
    item_id: str,
    body: PantryItemUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    hid = _household_id(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    result = await db.execute(
        select(PantryItem).where(
            PantryItem.id == item_id,
            PantryItem.household_id == hid,
            PantryItem.is_active == True,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        return APIResponse.fail("NOT_FOUND", "Pantry item not found.")

    item.estimated_qty_remaining = body.estimated_qty_remaining
    await db.commit()
    await db.refresh(item)
    return APIResponse.ok({"item": _serialize(item)})


@router.delete("/{item_id}", response_model=APIResponse)
async def delete_pantry_item(
    item_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    hid = _household_id(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    result = await db.execute(
        select(PantryItem).where(
            PantryItem.id == item_id,
            PantryItem.household_id == hid,
            PantryItem.is_active == True,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        return APIResponse.fail("NOT_FOUND", "Pantry item not found.")

    item.is_active = False
    await db.commit()
    return APIResponse.ok({"deleted": True})
