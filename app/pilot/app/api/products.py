"""
Shared product search endpoint — wraps Swiggy search_products MCP.
No basket-state guard; requires only an authenticated session.
"""
from fastapi import APIRouter, Request, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.schemas.common import APIResponse
from app.services.auth_service import AuthService
from app.mcp.swiggy import SwiggyMCPClient
from app.models.db import HouseholdPreferences, Address

router = APIRouter(prefix="/products", tags=["products"])


def _hid(request: Request) -> str | None:
    return request.session.get("household_id")


async def _get_swiggy_address_id(db: AsyncSession, hid: str) -> str | None:
    prefs = await db.scalar(select(HouseholdPreferences).where(HouseholdPreferences.household_id == hid))
    if not prefs or not prefs.preferred_address_id:
        return None
    addr = await db.get(Address, prefs.preferred_address_id)
    return addr.swiggy_address_id if addr else None


@router.get("/search", response_model=APIResponse)
async def search_products(
    request: Request,
    q: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
):
    hid = _hid(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    try:
        token = await AuthService(db).get_valid_token(hid)
    except Exception:
        return APIResponse.fail("TOKEN_EXPIRED", "Swiggy session expired. Please reconnect.")
    swiggy_address_id = await _get_swiggy_address_id(db, hid)
    if not swiggy_address_id:
        return APIResponse.fail("NO_ADDRESS", "No delivery address configured.")

    mcp = SwiggyMCPClient(token)

    try:
        results = await mcp.search_products(q, swiggy_address_id)
    except Exception as e:
        return APIResponse.fail("MCP_ERROR", f"Product search failed: {e}")

    items = []
    for r in (results.products if results else []):
        items.append({
            "swiggy_product_id": r.sku_id,
            "name": r.name,
            "price": r.price or 0,
            "brand": r.brand,
            "image_url": r.image_url,
        })

    return APIResponse.ok(items)
