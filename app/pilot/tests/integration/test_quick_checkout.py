"""
Integration tests — quick_checkout.checkout() (app/services/quick_checkout.py)

No coverage existed for the Quick Order checkout path before this file —
confirmed by search before writing (tasks/features/ai-ordering-assistant.md,
Design §0: extracting api/quick.py's inline checkout logic into a real
service function is a prerequisite for the AI assistant's checkout_basket
tool, and it needs its own first-ever verification before anything else is
built on top of it).

Covers every response code the function can return, plus the two real
paths (dry-run and live-via-mocked-MCP) that persist an Order.
"""

import pytest
from unittest.mock import patch

from sqlalchemy import select

from app.models.db import Order, OrderItem, ItemSignal
from app.services import quick_basket
from app.services import quick_checkout
from tests.integration.conftest import create_household


async def _seed_basket_item(household_id: str, **overrides) -> dict:
    item = {
        "item_name": "Tata Salt", "brand": "Tata", "sku_id": "sku_tata_salt_001",
        "spin_id": "spin_tata_salt_001", "category": "staples", "unit": "1 kg",
        "quantity": 2, "unit_price": 28.0, "in_stock": True,
    }
    item.update(overrides)
    return await quick_basket.add_item(household_id, item)


@pytest.fixture(autouse=True)
async def _clear_basket_between_tests():
    # quick_basket is Redis-backed, keyed by household_id — each test uses a
    # fresh household_id (create_household has no fixed id), so no explicit
    # cleanup is strictly required, but this guards against TTL-related
    # cross-test bleed if that ever changes.
    yield


@pytest.mark.asyncio
async def test_empty_basket_returns_empty_basket_code(db):
    household_id = await create_household(db)
    result = await quick_checkout.checkout(household_id, db)
    assert result == {"success": False, "code": "EMPTY_BASKET", "message": "Basket is empty."}


@pytest.mark.asyncio
async def test_token_expired_returns_token_expired_code(db):
    household_id = await create_household(db)
    await _seed_basket_item(household_id)

    from app.utils.exceptions import TokenExpiredError
    with patch(
        "app.services.quick_checkout._get_access_token",
        side_effect=TokenExpiredError("expired"),
    ):
        result = await quick_checkout.checkout(household_id, db, swiggy_address_id_override="addr_1")

    assert result["success"] is False
    assert result["code"] == "TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_no_address_returns_no_address_code(db):
    # create_household leaves preferred_address_id unset — no override passed here.
    household_id = await create_household(db)
    await _seed_basket_item(household_id)

    result = await quick_checkout.checkout(household_id, db)
    assert result["success"] is False
    assert result["code"] == "NO_ADDRESS"


@pytest.mark.asyncio
async def test_no_skus_in_basket_returns_no_skus_code(db):
    household_id = await create_household(db)
    await _seed_basket_item(household_id, sku_id=None)

    with patch("app.config.get_settings") as mock_settings:
        mock_settings.return_value.pantrypilot_dry_run = False
        result = await quick_checkout.checkout(household_id, db, swiggy_address_id_override="addr_1")

    assert result["success"] is False
    assert result["code"] == "NO_SKUS"


@pytest.mark.asyncio
async def test_cart_locked_returns_cart_locked_code(db, monkeypatch):
    # Speed up the blocking-acquire wait for this test only — production
    # behavior (_CART_LOCK_BLOCKING=10s) is unaffected outside this test.
    monkeypatch.setattr(quick_checkout, "_CART_LOCK_BLOCKING", 1)

    household_id = await create_household(db)
    await _seed_basket_item(household_id)

    from app.redis import get_redis
    redis = await get_redis()
    lock_key = f"routine_cart_lock:{household_id}"
    holder_lock = redis.lock(lock_key, timeout=30)
    acquired = await holder_lock.acquire(blocking=False)
    assert acquired, "test setup: failed to pre-acquire the lock"

    try:
        result = await quick_checkout.checkout(household_id, db, swiggy_address_id_override="addr_1")
    finally:
        await holder_lock.release()

    assert result["success"] is False
    assert result["code"] == "CART_LOCKED"


@pytest.mark.asyncio
async def test_dry_run_success_persists_order_and_clears_basket(db, monkeypatch):
    monkeypatch.setenv("PANTRYPILOT_DRY_RUN", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        household_id = await create_household(db)
        await _seed_basket_item(household_id, quantity=3, unit_price=28.0)

        result = await quick_checkout.checkout(household_id, db, swiggy_address_id_override="addr_1")

        assert result["success"] is True
        assert result["swiggy_order_id"].startswith("dry_run_")
        assert result["item_total"] == 84.0  # 3 * 28.0

        order = (await db.execute(select(Order).where(Order.household_id == household_id))).scalar_one()
        assert order.source == "quick_order"
        assert order.status == "placed"

        order_items = (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars().all()
        assert len(order_items) == 1
        assert order_items[0].product_name == "Tata Salt"

        signals = (await db.execute(
            select(ItemSignal).where(ItemSignal.household_id == household_id, ItemSignal.signal_type == "accepted")
        )).scalars().all()
        assert len(signals) == 1

        assert await quick_basket.get_basket(household_id) == []
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_live_checkout_via_mocked_mcp_persists_order(db, swiggy_mcp):
    household_id = await create_household(db)
    await _seed_basket_item(household_id, quantity=2, unit_price=28.0)

    result = await quick_checkout.checkout(household_id, db, swiggy_address_id_override="addr_home_001")

    assert result["success"] is True
    # SWIGGY_RESPONSES["checkout"] default fixture: orderId "241629385719397", totalAmount 520.0
    assert result["swiggy_order_id"] == "241629385719397"

    order = (await db.execute(select(Order).where(Order.household_id == household_id))).scalar_one()
    assert order.swiggy_order_id == "241629385719397"

    assert await quick_basket.get_basket(household_id) == []


@pytest.mark.asyncio
async def test_checkout_error_from_mcp_does_not_persist_order_or_clear_basket(db, swiggy_mcp):
    from tests.integration.conftest import _mcp_error
    # swiggy_mcp is session-scoped (conftest.py) — its overrides dict persists
    # across every test in the run, not just this one. Must reset in a
    # finally, or this leaks a permanent checkout failure into whichever
    # test happens to run next.
    swiggy_mcp["checkout"] = _mcp_error(500, "swiggy internal error")
    try:
        household_id = await create_household(db)
        await _seed_basket_item(household_id)

        result = await quick_checkout.checkout(household_id, db, swiggy_address_id_override="addr_home_001")

        assert result["success"] is False
        assert result["code"] == "CHECKOUT_ERROR"

        orders = (await db.execute(select(Order).where(Order.household_id == household_id))).scalars().all()
        assert orders == []
        # Basket must survive a failed checkout — nothing to reorder otherwise.
        assert await quick_basket.get_basket(household_id) != []
    finally:
        swiggy_mcp["checkout"] = None


@pytest.mark.asyncio
async def test_route_delegates_to_checkout_service(app_client, db, swiggy_mcp):
    """Thin end-to-end check that api/quick.py's POST /checkout route still
    produces the same response shape after delegating to quick_checkout —
    the refactor changed where the logic lives, not what it returns."""
    from tests.integration.conftest import set_session

    household_id = await create_household(db)
    await _seed_basket_item(household_id)
    set_session(app_client, household_id)

    resp = await app_client.post("/v1/quick/checkout", json={"swiggy_address_id": "addr_home_001"})
    body = resp.json()

    assert body["success"] is True
    assert body["data"]["swiggy_order_id"] == "241629385719397"
    assert "success" not in body["data"]  # internal flag stripped before returning to the client
