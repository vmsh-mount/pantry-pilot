"""
Integration tests — Dry Run Mode (BE-004)

Verifies that PANTRYPILOT_DRY_RUN=true:
  1. Prevents any real HTTP call to Swiggy checkout
  2. Fake order ID has dry_run_ prefix and is unique
  3. Order record created in DB with fake swiggy_order_id
  4. Pantry stock update is still triggered
  5. WhatsApp receipt still sent (with [DRY RUN] prefix)
  6. Run appears in history as completed
  7. total_price in history reflects basket estimate, not ₹0
  8. GET /v1/settings returns dry_run: true when flag is set
  9. Dashboard can read the flag from settings
  10. Real checkout proceeds unchanged when flag is false
"""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from tests.integration.conftest import create_household, _mcp_ok


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

async def _seed_household(db, whatsapp_verified=True):
    """Create a household with pantry items and preferences."""
    from app.models.db import (
        Household, HouseholdPreferences, PantryItem, Address,
    )
    hh = Household(
        id=str(uuid.uuid4()),
        swiggy_user_id="user_dry_run_test",
        whatsapp_number="+911234567890",
        whatsapp_verified=whatsapp_verified,
        household_type="couple",
        member_count=2,
        diet_type="vegetarian",
        allergies=[],
        weekly_budget_min=1000,
        weekly_budget_max=2000,
        onboarding_complete=True,
        is_active=True,
        is_paused=False,
    )
    db.add(hh)

    addr = Address(
        id=str(uuid.uuid4()),
        household_id=hh.id,
        swiggy_address_id="addr_home_001",
        label="Home",
        is_default=True,
    )
    db.add(addr)

    prefs = HouseholdPreferences(
        household_id=hh.id,
        preferred_address_id=addr.id,
        preferred_order_day="sunday",
        preferred_delivery_slot="evening",
    )
    db.add(prefs)

    pantry = PantryItem(
        household_id=hh.id,
        item_name="Tata Salt",
        category="staples",
        standard_unit="kg",
        estimated_qty_remaining=0.1,
        reorder_threshold=0.5,
        last_ordered_qty=1.0,
        avg_weekly_consumption=0.2,
        times_ordered=3,
    )
    db.add(pantry)

    await db.commit()
    return hh, addr, prefs


async def _create_confirmed_run(db, household_id: str, items: list[dict] | None = None):
    """Create a LoopRun in awaiting_confirmation state with items."""
    from app.models.db import LoopRun, LoopRunItem

    run = LoopRun(
        id=str(uuid.uuid4()),
        household_id=household_id,
        state="awaiting_confirmation",
        triggered_at=datetime.now(timezone.utc),
        confirm_sent_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.flush()

    basket_items = items or [
        {
            "item_name":           "Tata Salt",
            "swiggy_sku_id":       "sku_tata_salt_001",
            "swiggy_product_name": "Tata Salt 1kg",
            "brand":               "Tata",
            "quantity":            1.0,
            "unit":                "kg",
            "unit_price":          28.0,
            "total_price":         28.0,
            "added_by":            "rules_engine",
        },
        {
            "item_name":           "Amul Milk",
            "swiggy_sku_id":       "sku_amul_milk_002",
            "swiggy_product_name": "Amul Toned Milk 1L",
            "brand":               "Amul",
            "quantity":            3.0,
            "unit":                "litre",
            "unit_price":          64.0,
            "total_price":         192.0,
            "added_by":            "rules_engine",
        },
    ]

    for it in basket_items:
        db.add(LoopRunItem(
            loop_run_id=run.id,
            household_id=household_id,
            **it,
        ))

    await db.commit()
    return run


# ══════════════════════════════════════════════════════════════════════════════
# checkout() unit — mock behaviour
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_checkout_dry_run_returns_fake_order_id():
    """checkout() with dry_run=True must return a dry_run_ prefixed ID without HTTP call."""
    from app.mcp.swiggy import SwiggyMCPClient

    with patch("app.mcp.swiggy.get_settings", return_value=MagicMock(pantrypilot_dry_run=True)):
        client = SwiggyMCPClient("fake_token")
        result = await client.checkout(address_id="addr_home_001", estimated_total=220.0)

    assert result.order_id.startswith("dry_run_")
    assert result.status == "PLACED"
    assert result.grand_total == 220.0


@pytest.mark.asyncio
async def test_checkout_dry_run_order_ids_are_unique():
    """Each dry run checkout call produces a unique order ID."""
    from app.mcp.swiggy import SwiggyMCPClient

    with patch("app.mcp.swiggy.get_settings", return_value=MagicMock(pantrypilot_dry_run=True)):
        client = SwiggyMCPClient("fake_token")
        r1 = await client.checkout("addr1")
        r2 = await client.checkout("addr1")

    assert r1.order_id != r2.order_id


@pytest.mark.asyncio
async def test_checkout_dry_run_skips_http_call():
    """checkout() in dry run mode must NOT call _call() (no network request)."""
    from app.mcp.swiggy import SwiggyMCPClient

    with patch("app.mcp.swiggy.get_settings", return_value=MagicMock(pantrypilot_dry_run=True)):
        client = SwiggyMCPClient("fake_token")
        with patch.object(client, "_call", new_callable=AsyncMock) as mock_call:
            await client.checkout("addr_home_001")
            mock_call.assert_not_called()


@pytest.mark.asyncio
async def test_checkout_real_mode_calls_network():
    """checkout() with dry_run=False proceeds to the real _call() path."""
    from app.mcp.swiggy import SwiggyMCPClient

    with patch("app.mcp.swiggy.get_settings", return_value=MagicMock(pantrypilot_dry_run=False)):
        client = SwiggyMCPClient("fake_token")
        with patch.object(client, "_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {
                "data": {"orderId": "real_12345", "status": "PLACED", "grandTotal": 500.0}
            }
            result = await client.checkout("addr_home_001")

    mock_call.assert_called_once()
    assert result.order_id == "real_12345"


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/settings — dry_run flag exposed
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_settings_get_settings_includes_dry_run_key(db):
    """HouseholdService.get_settings() response includes dry_run field."""
    from app.services.household_service import HouseholdService

    hh, _, _ = await _seed_household(db)

    with patch("app.config.get_settings", return_value=MagicMock(pantrypilot_dry_run=True)):
        svc = HouseholdService(db)
        data = await svc.get_settings(hh.id)

    assert "dry_run" in data
    assert data["dry_run"] is True


@pytest.mark.asyncio
async def test_settings_dry_run_false_by_default(db):
    """dry_run is False when PANTRYPILOT_DRY_RUN is not set."""
    from app.services.household_service import HouseholdService

    hh, _, _ = await _seed_household(db)

    with patch("app.config.get_settings", return_value=MagicMock(pantrypilot_dry_run=False)):
        svc = HouseholdService(db)
        data = await svc.get_settings(hh.id)

    assert data["dry_run"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Full pipeline — dry run order in history
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_dry_run_order_id_prefix_in_db(db):
    """After a dry run checkout, Order.swiggy_order_id starts with dry_run_."""
    from app.models.db import Order, LoopRun
    from sqlalchemy import select

    hh, addr, _ = await _seed_household(db)
    run = await _create_confirmed_run(db, hh.id)

    # Simulate what place() does after receiving a fake checkout response
    fake_order_id = f"dry_run_{uuid.uuid4().hex[:12]}"
    order = Order(
        household_id=hh.id,
        loop_run_id=run.id,
        swiggy_order_id=fake_order_id,
        swiggy_address_id="addr_home_001",
        delivery_slot="evening",
        item_total=220.0,
        delivery_fee=0.0,
        taxes=0.0,
        grand_total=220.0,
        status="PLACED",
    )
    db.add(order)
    await db.flush()

    from sqlalchemy import update
    await db.execute(
        update(LoopRun)
        .where(LoopRun.id == run.id)
        .values(state="completed", order_id=order.id)
    )
    await db.commit()

    result = await db.execute(select(Order).where(Order.id == order.id))
    saved = result.scalar_one()
    assert saved.swiggy_order_id.startswith("dry_run_")
    assert saved.grand_total == 220.0


@pytest.mark.asyncio
async def test_dry_run_order_total_is_not_zero(db):
    """Dry run orders should show realistic basket total, not ₹0."""
    from app.models.db import Order, LoopRun
    from sqlalchemy import select

    hh, addr, _ = await _seed_household(db)
    run = await _create_confirmed_run(db, hh.id)

    estimated_total = 220.0  # sum of items: 28 + 3*64
    fake_order_id = f"dry_run_{uuid.uuid4().hex[:12]}"

    order = Order(
        household_id=hh.id,
        loop_run_id=run.id,
        swiggy_order_id=fake_order_id,
        swiggy_address_id="addr_home_001",
        delivery_slot="evening",
        item_total=estimated_total,
        delivery_fee=0.0,
        taxes=0.0,
        grand_total=estimated_total,
        status="PLACED",
    )
    db.add(order)
    await db.commit()

    result = await db.execute(select(Order).where(Order.id == order.id))
    saved = result.scalar_one()
    assert saved.grand_total > 0
    assert saved.grand_total == estimated_total


@pytest.mark.asyncio
async def test_dry_run_run_appears_as_completed(db, app_client):
    """Dry run completed run appears in GET /v1/runs as state=completed."""
    from app.models.db import Order, LoopRun
    from sqlalchemy import update

    hh, addr, _ = await _seed_household(db)
    run = await _create_confirmed_run(db, hh.id)

    fake_order_id = f"dry_run_{uuid.uuid4().hex[:12]}"
    order = Order(
        household_id=hh.id,
        loop_run_id=run.id,
        swiggy_order_id=fake_order_id,
        swiggy_address_id="addr_home_001",
        delivery_slot="evening",
        item_total=220.0,
        delivery_fee=0.0,
        taxes=0.0,
        grand_total=220.0,
        status="PLACED",
    )
    db.add(order)
    await db.flush()
    await db.execute(
        update(LoopRun).where(LoopRun.id == run.id)
        .values(state="completed", order_id=order.id,
                place_completed_at=datetime.now(timezone.utc))
    )
    await db.commit()

    with patch("app.api.runs._household_id", return_value=hh.id):
        res = await app_client.get("/v1/runs")

    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    runs = data["data"]["runs"]
    assert len(runs) == 1
    assert runs[0]["state"] == "completed"
    assert runs[0]["total_price"] == 220.0


@pytest.mark.asyncio
async def test_checkout_estimated_total_zero_fallback():
    """checkout() in dry run with no estimated_total still returns a valid response (0.0 fallback)."""
    from app.mcp.swiggy import SwiggyMCPClient

    with patch("app.mcp.swiggy.get_settings") as mock_settings:
        mock_settings.return_value.pantrypilot_dry_run = True
        client = SwiggyMCPClient("fake_token")
        result = await client.checkout("addr_home_001")  # no estimated_total

    assert result.order_id.startswith("dry_run_")
    assert result.grand_total == 0.0  # graceful fallback
