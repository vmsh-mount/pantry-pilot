"""
Integration tests — Orders API

Flows covered:
  1.  GET /orders — returns Swiggy order list with correct shape
  2.  GET /orders — PantryPilot orders annotated via_pantrypilot=True
  3.  GET /orders — Swiggy orders with no PantryPilot orders → all False
  4.  GET /orders — MCP failure → error response (not 500)
  5.  GET /orders — unauthenticated → NOT_AUTHENTICATED
  6.  GET /orders — order item names populated in preview_items
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from tests.integration.conftest import create_household, SWIGGY_RESPONSES, _mcp_ok, _mcp_error


async def auth_session(client, household_id: str):
    from tests.integration.conftest import encode_session
    client.cookies.set("session", encode_session(household_id))


# ══════════════════════════════════════════════════════════════════════════════
# 1. Returns correct shape
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_orders_returns_list(app_client, db, swiggy_mcp):
    """GET /v1/orders returns a list of orders with expected fields."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/orders")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    orders = data["data"]["orders"]
    assert isinstance(orders, list)
    assert len(orders) == 2   # SWIGGY_RESPONSES has 2 orders

    first = orders[0]
    assert first["order_id"]   == "241629385719397"
    assert first["total"]      == 266.0
    assert first["item_count"] == 4
    assert "placed_at" in first
    assert "via_pantrypilot" in first


# ══════════════════════════════════════════════════════════════════════════════
# 2. PantryPilot orders annotated
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_orders_pantrypilot_annotation(app_client, db, swiggy_mcp):
    """Order placed via PantryPilot has via_pantrypilot=True."""
    from app.models.db import Order

    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    # Record the first Swiggy order as placed via PantryPilot
    db.add(Order(
        household_id      = household_id,
        swiggy_order_id   = "241629385719397",
        swiggy_address_id = "addr_home_001",
        loop_run_id       = None,
        status            = "DELIVERED",
        item_total        = 248.0,
        delivery_fee      = 0.0,
        taxes             = 18.0,
        grand_total       = 266.0,
        placed_at         = datetime.now(timezone.utc) - timedelta(days=1),
    ))
    await db.commit()

    resp = await app_client.get("/v1/orders")
    orders = resp.json()["data"]["orders"]

    pilot_order = next(o for o in orders if o["order_id"] == "241629385719397")
    other_order = next(o for o in orders if o["order_id"] == "241513241502158")

    assert pilot_order["via_pantrypilot"] is True
    assert other_order["via_pantrypilot"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 3. No PantryPilot orders → all False
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_orders_no_pantrypilot_all_false(app_client, db, swiggy_mcp):
    """No PantryPilot orders in DB → all via_pantrypilot=False."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/orders")
    orders = resp.json()["data"]["orders"]
    assert all(o["via_pantrypilot"] is False for o in orders)


# ══════════════════════════════════════════════════════════════════════════════
# 4. MCP failure → graceful error
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_orders_mcp_failure_returns_error(app_client, db):
    """Swiggy MCP returns 500 → API returns error, not 500."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    async def _failing_post(self, url, *, json=None, **kwargs):
        return _mcp_error(500, "Swiggy server error")

    with patch("httpx.AsyncClient.post", new=_failing_post):
        resp = await app_client.get("/v1/orders")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "MCP_ERROR" in body["error"]["code"] or body["error"]["message"] != ""


# ══════════════════════════════════════════════════════════════════════════════
# 5. Unauthenticated
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_orders_unauthenticated(app_client):
    """No session → NOT_AUTHENTICATED."""
    resp = await app_client.get("/v1/orders")
    body = resp.json()
    assert body["success"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 6. Preview items populated
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_orders_preview_items_populated(app_client, db, swiggy_mcp):
    """preview_items shows item names from the order's items list."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/orders")
    orders = resp.json()["data"]["orders"]
    first = orders[0]

    # SWIGGY_RESPONSES["get_orders"] has items with "name" key
    assert len(first["preview_items"]) > 0
    assert "Tata Salt" in first["preview_items"] or "Amul Toned Milk" in first["preview_items"]
