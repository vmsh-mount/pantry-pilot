from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.schemas.common import APIResponse

router = APIRouter(prefix="/orders", tags=["orders"])


def _get_household_id(request: Request) -> str | None:
    return request.session.get("household_id")


@router.get("", response_model=APIResponse)
async def list_orders(request: Request, db: AsyncSession = Depends(get_db)):
    """Return last 20 Swiggy orders, annotating which were placed via PantryPilot."""
    from app.services.auth_service import AuthService
    from app.mcp.swiggy import SwiggyMCPClient
    from app.models.db import Order

    household_id = _get_household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    token = await AuthService(db).get_valid_token(household_id)
    mcp = SwiggyMCPClient(token)

    try:
        raw_orders = await mcp.get_orders(limit=20)
    except Exception as e:
        return APIResponse.fail("MCP_ERROR", str(e))

    # Fetch all orders placed via PantryPilot for this household
    placed_result = await db.execute(
        select(Order.swiggy_order_id, Order.id).where(Order.household_id == household_id)
    )
    pilot_order_map = {row.swiggy_order_id: str(row.id) for row in placed_result.all()}

    orders = []
    for o in raw_orders:
        # MCPOrderSummary is a Pydantic model — access fields directly
        order_id   = o.order_id
        placed_at  = o.placed_at
        total      = o.grand_total
        item_count = o.item_count
        items      = o.items  # list of raw dicts: [{name, quantity, itemId}]

        item_names = [
            it.get("name") or it.get("item_name") or ""
            for it in items
            if isinstance(it, dict)
        ]

        orders.append({
            "order_id":        order_id,
            "placed_at":       placed_at,
            "total":           float(total),
            "item_count":      item_count,
            "preview_items":   item_names[:3],
            "via_pantrypilot":       order_id in pilot_order_map,
            "pantrypilot_order_id":  pilot_order_map.get(order_id),
        })

    return APIResponse.ok({"orders": orders})
