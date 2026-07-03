"""
Integration tests — Basket editing API endpoints

Endpoints under test:
  GET  /v1/basket/pending         (response schema fix — id/added_by/category now included)
  DELETE /v1/basket/item/{id}     (remove single item)
  GET  /v1/basket/search?q=       (live Swiggy search proxy)
  POST /v1/basket/item            (add item from search result)

Flows covered:
  GET /v1/basket/pending
    1.  Response includes `id` on each item row
    2.  Response includes `added_by` and `is_substitution` on each item row

  DELETE /v1/basket/item/{id}
    3.  Happy path — item removed, totals recalculated
    4.  Unauthenticated → NOT_AUTHENTICATED
    5.  No pending basket → NO_PENDING_BASKET
    6.  Unknown item_id → ITEM_NOT_FOUND
    7.  item_id from a different run → ITEM_NOT_FOUND (cross-run guard)
    8.  Remove last item → basket is empty, item_count=0
    9.  Estimated total updated correctly after removal

  GET /v1/basket/search?q=
    10. Happy path — returns list of products from Swiggy
    11. Unauthenticated → NOT_AUTHENTICATED
    12. Query too short (< 2 chars) → QUERY_TOO_SHORT
    13. Token expired → TOKEN_EXPIRED
    14. Swiggy MCP error → SEARCH_FAILED (retryable)
    15. Out-of-stock products excluded from results

  POST /v1/basket/item
    16. Happy path — item added, totals updated
    17. Unauthenticated → NOT_AUTHENTICATED
    18. No pending basket → NO_PENDING_BASKET
    19. Added item appears in subsequent GET /pending
    20. added_by is "user_added" on new item
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from tests.integration.conftest import (
    create_household,
    SWIGGY_RESPONSES,
    _mcp_ok,
    _mcp_error,
)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

async def auth_session(client, household_id: str):
    from tests.integration.conftest import encode_session
    client.cookies.set("session", encode_session(household_id))


async def create_household_with_address(db, swiggy_user_id="swiggy_edit_001"):
    """
    Full household with a linked Address row so search_items can resolve
    swiggy_address_id. create_household() leaves preferred_address_id=None.
    """
    from app.models.db import Address, HouseholdPreferences
    from sqlalchemy import update

    household_id = await create_household(db, swiggy_user_id)

    addr = Address(
        household_id      = household_id,
        swiggy_address_id = "addr_home_001",
        label             = "Home",
        is_default        = True,
    )
    db.add(addr)
    await db.flush()

    await db.execute(
        update(HouseholdPreferences)
        .where(HouseholdPreferences.household_id == household_id)
        .values(preferred_address_id=addr.id)
    )
    await db.commit()
    return household_id


async def seed_loop_run(db, household_id, state="awaiting_confirmation"):
    from app.models.db import LoopRun
    run = LoopRun(
        household_id = household_id,
        trigger_type = "scheduled",
        state        = state,
        triggered_at = datetime.now(timezone.utc),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def seed_item(db, loop_run_id, household_id, name="Tata Salt", price=28.0, added_by="rules_engine"):
    from app.models.db import LoopRunItem
    item = LoopRunItem(
        loop_run_id         = loop_run_id,
        household_id        = household_id,
        item_name           = name,
        swiggy_sku_id       = f"sku_{name.lower().replace(' ', '_')}",
        swiggy_product_name = f"{name} 1kg",
        brand               = "Test Brand",
        quantity            = 1.0,
        unit                = "kg",
        unit_price          = price,
        total_price         = price,
        added_by            = added_by,
        is_substitution     = False,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/basket/pending — response schema
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pending_items_include_id(app_client, db):
    """Each item in GET /pending response must include the `id` field."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run  = await seed_loop_run(db, household_id)
    item = await seed_item(db, run.id, household_id)

    resp = await app_client.get("/v1/basket/pending")
    data = resp.json()["data"]

    assert data["pending"] is True
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == item.id


@pytest.mark.asyncio
async def test_pending_items_include_metadata_fields(app_client, db):
    """Items include added_by, is_substitution, original_item_name."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run  = await seed_loop_run(db, household_id)
    await seed_item(db, run.id, household_id, added_by="llm")

    resp = await app_client.get("/v1/basket/pending")
    item = resp.json()["data"]["items"][0]

    assert "added_by"           in item
    assert "is_substitution"    in item
    assert "original_item_name" in item
    assert item["added_by"] == "llm"


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /v1/basket/item/{id}
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_remove_item_happy_path(app_client, db):
    """Remove a basket item → success, item_count decremented, total updated."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run  = await seed_loop_run(db, household_id)
    item = await seed_item(db, run.id, household_id, price=28.0)

    resp = await app_client.delete(f"/v1/basket/item/{item.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["removed"] is True
    assert data["data"]["item_count"]      == 0
    assert data["data"]["estimated_total"] == 0.0


@pytest.mark.asyncio
async def test_remove_item_unauthenticated(app_client, db):
    """No session → NOT_AUTHENTICATED."""
    resp = await app_client.delete("/v1/basket/item/00000000-0000-0000-0000-000000000000")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_remove_item_no_pending_basket(app_client, db):
    """Household has no awaiting_confirmation run → NO_PENDING_BASKET."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.delete("/v1/basket/item/00000000-0000-0000-0000-000000000001")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NO_PENDING_BASKET"


@pytest.mark.asyncio
async def test_remove_item_unknown_id(app_client, db):
    """Valid pending run but item UUID not in basket → ITEM_NOT_FOUND."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    await seed_loop_run(db, household_id)

    resp = await app_client.delete("/v1/basket/item/00000000-0000-0000-0000-000000000002")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "ITEM_NOT_FOUND"


@pytest.mark.asyncio
async def test_remove_item_cross_run_blocked(app_client, db):
    """
    Item belongs to a completed run (not the pending one).
    The DELETE guard uses the pending run's id — so this item is not found.
    """
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    old_run = await seed_loop_run(db, household_id, state="completed")
    item    = await seed_item(db, old_run.id, household_id)
    # Now create the actual awaiting run (the endpoint will find this one)
    await seed_loop_run(db, household_id, state="awaiting_confirmation")

    resp = await app_client.delete(f"/v1/basket/item/{item.id}")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "ITEM_NOT_FOUND"


@pytest.mark.asyncio
async def test_remove_last_item_basket_empty(app_client, db):
    """Removing the only item leaves basket with item_count=0."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run  = await seed_loop_run(db, household_id)
    item = await seed_item(db, run.id, household_id, price=64.0)

    resp = await app_client.delete(f"/v1/basket/item/{item.id}")

    data = resp.json()["data"]
    assert data["item_count"]      == 0
    assert data["estimated_total"] == 0.0


@pytest.mark.asyncio
async def test_remove_item_total_recalculated(app_client, db):
    """Removing one of two items updates total to only the remaining item."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run   = await seed_loop_run(db, household_id)
    item1 = await seed_item(db, run.id, household_id, name="Salt",  price=28.0)
    _     = await seed_item(db, run.id, household_id, name="Milk",  price=64.0)

    resp = await app_client.delete(f"/v1/basket/item/{item1.id}")

    data = resp.json()["data"]
    assert data["item_count"]      == 1
    assert data["estimated_total"] == 64.0


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/basket/search?q=
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_search_happy_path(app_client, db, swiggy_mcp):
    """Authenticated search with a valid query returns product list."""
    household_id = await create_household_with_address(db)
    await auth_session(app_client, household_id)

    # swiggy_mcp is already mocked to return SWIGGY_RESPONSES["search_products"]
    resp = await app_client.get("/v1/basket/search?q=salt")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    results = data["data"]["results"]
    assert isinstance(results, list)
    assert len(results) >= 1
    # Each result must have the required fields
    for r in results:
        assert "swiggy_product_id" in r
        assert "name"              in r
        assert "price"             in r


@pytest.mark.asyncio
async def test_search_unauthenticated(app_client):
    """No session → NOT_AUTHENTICATED."""
    resp = await app_client.get("/v1/basket/search?q=milk")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_search_query_too_short(app_client, db):
    """Single character query → QUERY_TOO_SHORT."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/basket/search?q=a")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "QUERY_TOO_SHORT"


@pytest.mark.asyncio
async def test_search_missing_query_param(app_client, db):
    """Missing q param → error (FastAPI 422 or QUERY_TOO_SHORT)."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/basket/search")
    assert resp.status_code in (200, 422)


@pytest.mark.asyncio
async def test_search_token_expired(app_client, db):
    """Expired Swiggy token → TOKEN_EXPIRED (not 500)."""
    household_id = await create_household_with_address(db)
    await auth_session(app_client, household_id)

    with patch(
        "app.services.auth_service.AuthService.get_valid_token",
        new=AsyncMock(side_effect=__import__("app.utils.exceptions", fromlist=["TokenExpiredError"]).TokenExpiredError("expired")),
    ):
        resp = await app_client.get("/v1/basket/search?q=milk")

    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "TOKEN_EXPIRED"
    assert body["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_search_mcp_error_returns_search_failed(app_client, db, swiggy_mcp):
    """Swiggy MCP 500 → SEARCH_FAILED with retryable=True."""
    from httpx import Response as HttpxResponse
    household_id = await create_household_with_address(db)
    await auth_session(app_client, household_id)

    # Override search_products to return a 500
    swiggy_mcp["search_products"] = HttpxResponse(500, json={"error": "server error"})

    try:
        resp = await app_client.get("/v1/basket/search?q=milk")
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "SEARCH_FAILED"
        assert body["error"]["retryable"] is True
    finally:
        del swiggy_mcp["search_products"]


@pytest.mark.asyncio
async def test_search_excludes_out_of_stock(app_client, db, swiggy_mcp):
    """Products with isInStockAndAvailable=False are excluded from results."""
    from httpx import Response as HttpxResponse
    import json

    household_id = await create_household_with_address(db)
    await auth_session(app_client, household_id)

    out_of_stock_response = {
        "products": [
            {
                "productId":   "sku_rare_001",
                "displayName": "Rare Item",
                "brand":       "Rare",
                "inStock":     False,
                "variations": [{
                    "spinId": "sku_rare_001", "displayName": "Rare Item 1kg",
                    "brandName": "Rare", "quantityDescription": "1 kg",
                    "isInStockAndAvailable": False,
                    "price": {"mrp": 100.0, "offerPrice": 100.0},
                }],
            }
        ],
        "totalCount": 1,
    }
    body = {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": out_of_stock_response}}
    swiggy_mcp["search_products"] = HttpxResponse(200, json=body)

    try:
        resp = await app_client.get("/v1/basket/search?q=rare")
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["results"] == []
    finally:
        del swiggy_mcp["search_products"]


# ══════════════════════════════════════════════════════════════════════════════
# POST /v1/basket/item
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_add_item_happy_path(app_client, db):
    """Add a product to pending basket → item appears in response."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run = await seed_loop_run(db, household_id)

    payload = {
        "swiggy_product_id": "sku_amul_milk_002",
        "name":              "Amul Toned Milk 1L",
        "price":             64.0,
        "brand":             "Amul",
        "image_url":         None,
        "category":          "dairy",
    }

    resp = await app_client.post("/v1/basket/item", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["added"] is True
    item = data["data"]["item"]
    assert item["item_name"]    == "Amul Toned Milk 1L"
    assert item["added_by"]     == "user_added"
    assert item["total_price"]  == 64.0
    assert item["id"] is not None


@pytest.mark.asyncio
async def test_add_item_unauthenticated(app_client):
    """No session → NOT_AUTHENTICATED."""
    payload = {"swiggy_product_id": "x", "name": "Test", "price": 10.0}
    resp = await app_client.post("/v1/basket/item", json=payload)
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_add_item_no_pending_basket(app_client, db):
    """No awaiting_confirmation run → NO_PENDING_BASKET."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    payload = {"swiggy_product_id": "x", "name": "Test", "price": 10.0}
    resp = await app_client.post("/v1/basket/item", json=payload)
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NO_PENDING_BASKET"


@pytest.mark.asyncio
async def test_add_item_appears_in_pending(app_client, db):
    """After POST /basket/item, the item is visible in GET /basket/pending."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    await seed_loop_run(db, household_id)

    payload = {
        "swiggy_product_id": "sku_amul_milk_002",
        "name":              "Amul Toned Milk 1L",
        "price":             64.0,
        "brand":             "Amul",
    }
    await app_client.post("/v1/basket/item", json=payload)

    resp = await app_client.get("/v1/basket/pending")
    data = resp.json()["data"]
    assert data["pending"]    is True
    assert data["item_count"] == 1
    assert data["items"][0]["item_name"] == "Amul Toned Milk 1L"
    assert data["items"][0]["added_by"]  == "user_added"


@pytest.mark.asyncio
async def test_add_item_total_updated(app_client, db):
    """Adding an item to a basket with existing items updates total correctly."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run = await seed_loop_run(db, household_id)
    await seed_item(db, run.id, household_id, name="Salt", price=28.0)

    payload = {"swiggy_product_id": "sku_milk", "name": "Milk", "price": 64.0}
    resp = await app_client.post("/v1/basket/item", json=payload)

    data = resp.json()["data"]
    assert data["item_count"]      == 2
    assert data["estimated_total"] == 92.0


@pytest.mark.asyncio
async def test_add_item_added_by_is_user_added(app_client, db):
    """Items added via POST /basket/item always have added_by=user_added."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    await seed_loop_run(db, household_id)

    payload = {"swiggy_product_id": "sku_x", "name": "Oats", "price": 120.0}
    resp = await app_client.post("/v1/basket/item", json=payload)

    item = resp.json()["data"]["item"]
    assert item["added_by"] == "user_added"


# ══════════════════════════════════════════════════════════════════════════════
# End-to-end edit sequence: remove + add + confirm
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_edit_then_confirm_flow(app_client, db):
    """
    Full edit flow: start with 2 items, remove one, add one,
    then confirm. Confirm must succeed (non-empty basket after edits).
    """
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run   = await seed_loop_run(db, household_id)
    item1 = await seed_item(db, run.id, household_id, name="Salt", price=28.0)
    _     = await seed_item(db, run.id, household_id, name="Ghee", price=295.0)

    # Remove Ghee
    remove_resp = await app_client.delete(f"/v1/basket/item/{item1.id}")
    assert remove_resp.json()["success"] is True

    # Add Oats
    add_resp = await app_client.post("/v1/basket/item", json={
        "swiggy_product_id": "sku_oats", "name": "Oats", "price": 120.0,
    })
    assert add_resp.json()["success"] is True

    # Confirm basket
    with patch("app.tasks.planning.place_confirmed_order.delay") as mock_delay:
        confirm_resp = await app_client.post("/v1/basket/confirm")

    assert confirm_resp.json()["success"] is True
    mock_delay.assert_called_once()


@pytest.mark.asyncio
async def test_remove_all_then_confirm_blocked(app_client, db):
    """
    Remove all items from basket, then try to confirm.
    Must fail with EMPTY_BASKET — confirming an empty basket is rejected.
    """
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run  = await seed_loop_run(db, household_id)
    item = await seed_item(db, run.id, household_id)

    # Remove the only item
    await app_client.delete(f"/v1/basket/item/{item.id}")

    # Confirm should be rejected
    resp = await app_client.post("/v1/basket/confirm")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "EMPTY_BASKET"
