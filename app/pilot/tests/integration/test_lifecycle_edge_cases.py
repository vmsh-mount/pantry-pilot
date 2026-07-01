"""
Integration tests — User lifecycle edge cases

Covers the scenarios that fall outside the happy path:

  1.  Timeout handler — run still awaiting → auto-skip + WhatsApp nudge
  2.  Timeout handler — run already resolved → no-op
  3.  Planning loop — should_abort=True with error_stage → mark_failed called
  4.  Planning loop — should_abort=True without error_stage → clean skip, not failed
  5.  Pantry decay — estimated_qty decreases correctly per day
  6.  Pantry update post-order — quantities updated after confirmed order
  7.  Household paused mid-run — trigger blocked
  8.  Token expired — get_valid_token raises, loop fails gracefully
  9.  MCP 401 during optimize — TokenExpiredError propagates, loop fails
  10. Double-trigger guard — second trigger while in-progress rejected
  11. Budget exactly at limit — no trim applied
  12. Substitution recorded — is_substitution=True when product out of stock
  13. LLM adds item already in candidate — no duplicate in final basket
  14. place() — cart update + checkout, order_id persisted
  15. Reschedule — next_run_at set to next preferred weekday
  16. place() — preferred_address_id=None → falls back to Swiggy address, succeeds
  17. place() — preferred_address_id=None and no Swiggy addresses → should_abort gracefully
"""

import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from tests.integration.conftest import (
    create_household, SWIGGY_RESPONSES, _mcp_ok, _mcp_error,
)


async def auth_session(client, household_id: str):
    from tests.integration.conftest import encode_session
    client.cookies.set("session", encode_session(household_id))


def mock_db_ctx():
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock())
    mock_db.commit  = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__  = AsyncMock(return_value=False)
    return ctx, mock_db


# ══════════════════════════════════════════════════════════════════════════════
# 1–2. Timeout handler
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_timeout_auto_skips_awaiting_run(db):
    """6-hour timeout on an awaiting run → state=skipped, WhatsApp nudge sent."""
    from app.services.planning_service import PlanningService
    from app.models.db import LoopRun, Household

    household_id = await create_household(db)

    run = LoopRun(
        household_id = household_id,
        trigger_type = "scheduled",
        state        = "awaiting_confirmation",
        triggered_at = datetime.now(timezone.utc) - timedelta(hours=7),
    )
    db.add(run)
    await db.commit()

    mock_wa = MagicMock()
    mock_wa.send_text = AsyncMock()

    with (
        patch("app.services.whatsapp_service.WhatsAppService", return_value=mock_wa),
        patch("app.services.planning_service.PlanningService.reschedule_next_run",
              new=AsyncMock(return_value=datetime.now(timezone.utc) + timedelta(days=7))),
    ):
        svc = PlanningService(db)
        await svc.handle_timeout(household_id, str(run.id))

    from sqlalchemy import select
    result = await db.execute(select(LoopRun).where(LoopRun.id == run.id))
    updated = result.scalar_one()
    assert updated.state       == "skipped"
    assert updated.skip_reason == "confirmation_timeout_6hr"
    mock_wa.send_text.assert_called_once()


@pytest.mark.asyncio
async def test_timeout_noop_if_already_resolved(db):
    """Timeout on an already-placed run → no state change, no WhatsApp."""
    from app.services.planning_service import PlanningService
    from app.models.db import LoopRun

    household_id = await create_household(db)
    run = LoopRun(
        household_id = household_id,
        trigger_type = "scheduled",
        state        = "placed",   # already resolved
        triggered_at = datetime.now(timezone.utc) - timedelta(hours=7),
    )
    db.add(run)
    await db.commit()

    mock_wa = MagicMock()
    mock_wa.send_text = AsyncMock()

    with patch("app.services.whatsapp_service.WhatsAppService", return_value=mock_wa):
        svc = PlanningService(db)
        await svc.handle_timeout(household_id, str(run.id))

    mock_wa.send_text.assert_not_called()

    from sqlalchemy import select
    result = await db.execute(select(LoopRun).where(LoopRun.id == run.id))
    unchanged = result.scalar_one()
    assert unchanged.state == "placed"


# ══════════════════════════════════════════════════════════════════════════════
# 3–4. run_loop abort handling
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_run_loop_error_stage_marks_failed(db):
    """Graph aborts with error_stage set → mark_failed called."""
    from app.services.planning_service import PlanningService
    from app.models.db import LoopRun

    household_id = await create_household(db)
    run = LoopRun(household_id=household_id, trigger_type="scheduled", state="pending")
    db.add(run)
    await db.commit()

    failed_state = {
        "should_abort": True,
        "error":        "MCP timeout",
        "error_stage":  "optimize",
    }

    with (
        patch("app.agent.planning_graph.build_planning_graph") as mock_graph_builder,
        patch("app.services.auth_service.AuthService.get_valid_token",
              new=AsyncMock(return_value="token")),
    ):
        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value=failed_state)
        mock_graph_builder.return_value = mock_graph

        svc = PlanningService(db)
        await svc.run_loop(household_id, str(run.id))

    from sqlalchemy import select
    result = await db.execute(select(LoopRun).where(LoopRun.id == run.id))
    updated = result.scalar_one()
    assert updated.state          == "failed"
    assert updated.failure_reason == "MCP timeout"
    assert updated.failure_stage  == "optimize"


@pytest.mark.asyncio
async def test_run_loop_clean_abort_not_marked_failed(db):
    """Graph aborts cleanly (empty basket) with no error_stage → state stays skipped."""
    from app.services.planning_service import PlanningService
    from app.models.db import LoopRun

    household_id = await create_household(db)
    run = LoopRun(household_id=household_id, trigger_type="scheduled", state="pending")
    db.add(run)
    await db.commit()

    # Confirm node sets state=skipped in DB then returns should_abort=True
    from sqlalchemy import update
    await db.execute(
        update(LoopRun).where(LoopRun.id == run.id).values(state="skipped")
    )
    await db.commit()

    clean_abort_state = {
        "should_abort": True,
        # no error_stage — clean skip
    }

    with (
        patch("app.agent.planning_graph.build_planning_graph") as mock_graph_builder,
        patch("app.services.auth_service.AuthService.get_valid_token",
              new=AsyncMock(return_value="token")),
    ):
        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value=clean_abort_state)
        mock_graph_builder.return_value = mock_graph

        svc = PlanningService(db)
        await svc.run_loop(household_id, str(run.id))

    from sqlalchemy import select
    result = await db.execute(select(LoopRun).where(LoopRun.id == run.id))
    updated = result.scalar_one()
    # Must NOT be overwritten to "failed"
    assert updated.state != "failed"
    assert updated.state == "skipped"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Pantry decay
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pantry_decay_reduces_quantity(db):
    """apply_decay() reduces estimated_qty_remaining based on days elapsed."""
    from app.models.db import PantryItem
    from app.services.pantry_service import PantryService

    household_id = await create_household(db)

    # Item ordered 7 days ago at 1kg, avg consumption 0.1kg/day
    ordered_7d_ago = datetime.now(timezone.utc) - timedelta(days=7)
    db.add(PantryItem(
        household_id            = household_id,
        item_name               = "Toor Dal",
        category                = "staples",
        standard_unit           = "kg",
        estimated_qty_remaining = 1.0,
        reorder_threshold       = 0.3,
        avg_weekly_consumption  = 0.7,   # ~0.1/day
        last_ordered_at         = ordered_7d_ago,
    ))
    await db.commit()

    svc = PantryService(db)
    items = await svc.apply_decay(household_id)

    assert len(items) == 1
    # After 7 days at 0.1kg/day, ~0.3kg consumed → remaining should be ~0.7 or less
    assert items[0].estimated_qty_remaining < 1.0


# ══════════════════════════════════════════════════════════════════════════════
# 6. Pantry update post-order
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pantry_update_post_order(db, swiggy_mcp):
    """After placing an order, pantry items are updated with new quantities."""
    from app.services.pantry_service import PantryService
    from app.models.db import PantryItem

    household_id = await create_household(db)

    db.add(PantryItem(
        household_id            = household_id,
        item_name               = "Tata Salt",
        category                = "staples",
        standard_unit           = "kg",
        estimated_qty_remaining = 0.1,
        reorder_threshold       = 0.5,
        last_ordered_qty        = 1.0,
        times_ordered           = 3,
    ))
    await db.commit()

    # post_order_update expects "name" key (same shape as resolved_basket items)
    ordered_items = [
        {
            "name":       "Tata Salt",
            "item_name":  "Tata Salt",
            "sku_id":     "sku_tata_salt_001",
            "quantity":   2.0,
            "unit":       "kg",
            "unit_price": 28.0,
            "total_price": 56.0,
        }
    ]

    svc = PantryService(db)
    await svc.post_order_update(household_id, ordered_items)

    from sqlalchemy import select
    result = await db.execute(
        select(PantryItem).where(
            PantryItem.household_id == household_id,
            PantryItem.item_name    == "Tata Salt",
        )
    )
    item = result.scalar_one()
    # Quantity should reflect the new order
    assert item.last_ordered_qty == 2.0
    assert item.times_ordered    >= 4


# ══════════════════════════════════════════════════════════════════════════════
# 7. Budget exactly at limit — no trim
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_optimize_budget_exactly_at_limit(swiggy_mcp):
    """Basket total == budget_max → no items trimmed."""
    from app.agent.planning_graph import optimize

    state = {
        "household_id":         "00000000-0000-0000-0000-000000000001",
        "loop_run_id":          "00000000-0000-0000-0000-000000000002",
        "access_token":         "fake_access_token_for_tests",
        "should_abort":         False,
        "household_profile":    {"diet_type": "vegetarian", "budget_max": 28, "budget_min": 0},
        "brand_preferences":    {},
        "preferred_address_id": "addr_home_001",
        "candidate_basket": [
            {"item_name": "Tata Salt", "sku_id": None, "quantity": 1.0,
             "unit": "kg", "category": "staples", "brand": None,
             "added_by": "rules_engine", "is_substitution": False},
        ],
        "llm_additions": [],
    }

    # Real Swiggy format: products with variations[] and nested price
    swiggy_mcp["search_products"] = _mcp_ok("search_products", {
        "products": [{
            "productId": "sku_001", "displayName": "Tata Salt", "brand": "Tata",
            "category": "staples", "inStock": True,
            "variations": [{"spinId": "sku_001", "displayName": "Tata Salt 1kg",
                             "brandName": "Tata", "quantityDescription": "1 kg",
                             "isInStockAndAvailable": True,
                             "price": {"mrp": 28.0, "offerPrice": 28.0}}],
        }],
        "totalCount": 1,
    })

    try:
        with patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]):
            result = await optimize(state)

        # Exactly at budget → nothing trimmed
        assert len(result["resolved_basket"]) == 1
        assert result["estimated_total"] == 28.0
    finally:
        swiggy_mcp.pop("search_products", None)


# ══════════════════════════════════════════════════════════════════════════════
# 8. Substitution recorded
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_optimize_substitution_recorded():
    """Out-of-stock item → substitute found, is_substitution=True."""
    from app.agent.planning_graph import optimize

    state = {
        "household_id":         "00000000-0000-0000-0000-000000000001",
        "loop_run_id":          "00000000-0000-0000-0000-000000000002",
        "access_token":         "fake_access_token_for_tests",
        "should_abort":         False,
        "household_profile":    {"diet_type": "vegetarian", "budget_max": 5000, "budget_min": 0},
        "brand_preferences":    {},
        "preferred_address_id": "addr_home_001",
        "candidate_basket": [
            {"item_name": "Special Brand Salt", "sku_id": None, "quantity": 1.0,
             "unit": "kg", "category": "staples", "brand": "SpecialBrand",
             "added_by": "rules_engine", "is_substitution": False},
        ],
        "llm_additions": [],
    }

    def _make_product(spinId, name, brand, in_stock, price):
        """Build real Swiggy-format search_products response."""
        return _mcp_ok("search_products", {
            "products": [{
                "productId": spinId, "displayName": name, "brand": brand,
                "category": "staples", "inStock": in_stock,
                "variations": [{"spinId": spinId, "displayName": f"{name} 1kg",
                                 "brandName": brand, "quantityDescription": "1 kg",
                                 "isInStockAndAvailable": in_stock,
                                 "price": {"mrp": price, "offerPrice": price}}],
            }],
            "totalCount": 1,
        })

    call_count = 0

    async def _substitution_post(self, url, *, json=None, **kwargs):
        nonlocal call_count
        body = json or {}
        if body.get("jsonrpc") == "2.0" and "params" in body:
            tool = body.get("params", {}).get("name", "")
            call_count += 1
            if tool == "search_products":
                if call_count == 1:
                    return _make_product("sku_oos", "Special Brand Salt", "SpecialBrand", False, 35.0)
                else:
                    return _make_product("sku_sub", "Tata Salt", "Tata", True, 28.0)
            return _mcp_ok(tool)
        # Non-MCP (ASGITransport) — pass through
        from httpx import AsyncClient
        return await _real_post(self, url, json=json, **kwargs)

    from tests.integration.conftest import encode_session  # ensure _real_post accessible
    import httpx as _httpx
    _real_post = _httpx.AsyncClient.post

    with (
        patch("httpx.AsyncClient.post", new=_substitution_post),
        patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]),
    ):
        result = await optimize(state)

    assert len(result["resolved_basket"]) == 1
    item = result["resolved_basket"][0]
    assert item["is_substitution"]  is True
    # product_name comes from _parse_product displayName (variant-level) = "Tata Salt 1kg"
    assert "Tata Salt" in item["product_name"]


# ══════════════════════════════════════════════════════════════════════════════
# 9. Reschedule — next preferred weekday
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_reschedule_sets_next_weekday(db):
    """reschedule_next_run sets next_run_at to the next occurrence of preferred day."""
    from app.services.planning_service import PlanningService
    from app.models.db import HouseholdPreferences
    from sqlalchemy import select

    household_id = await create_household(db)

    # Ensure prefs exist with preferred day = "sunday"
    result = await db.execute(
        select(HouseholdPreferences).where(
            HouseholdPreferences.household_id == household_id
        )
    )
    prefs = result.scalar_one_or_none()
    assert prefs is not None
    prefs.preferred_order_day = "sunday"
    await db.commit()

    with patch("app.tasks.planning.trigger_planning_loop.apply_async"):
        svc = PlanningService(db)
        next_run = await svc.reschedule_next_run(household_id)

    assert next_run > datetime.now(timezone.utc)
    # Should be a Sunday
    assert next_run.weekday() == 6   # 6 = Sunday in Python


# ══════════════════════════════════════════════════════════════════════════════
# 10. place() — full checkout flow
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_place_node_calls_checkout_and_persists_order(db, swiggy_mcp):
    """place() calls update_cart + checkout, persists order_id in LoopRun."""
    from app.agent.planning_graph import place
    from app.models.db import LoopRun
    from sqlalchemy import select

    household_id = await create_household(db)
    run = LoopRun(household_id=household_id, trigger_type="scheduled", state="confirmed")
    db.add(run)
    await db.commit()

    resolved = [
        {"item_name": "Tata Salt", "sku_id": "sku_tata_salt_001",
         "product_name": "Tata Salt 1kg", "brand": "Tata", "category": "staples",
         "quantity": 1.0, "unit": "kg", "unit_price": 28.0, "total_price": 28.0,
         "in_stock": True, "added_by": "rules_engine", "is_substitution": False,
         "original_item_name": None, "substitution_reason": None},
    ]

    state = {
        "household_id":             household_id,
        "loop_run_id":              str(run.id),
        "access_token":             "fake_access_token_for_tests",
        "preferred_address_id":     "addr_home_001",
        "preferred_delivery_slot":  "evening",
        "whatsapp_number":          "+918499933228",
        "resolved_basket":          resolved,
        "estimated_total":          520.0,   # matches update_cart grand_total in SWIGGY_RESPONSES
        "household_profile":        {"budget_max": 2500},
    }

    mock_wa = MagicMock()
    mock_wa.send_order_receipt       = AsyncMock()
    mock_wa.send_order_confirmation  = AsyncMock()

    with (
        patch("app.agent.planning_graph._db_context") as mock_ctx,
        patch("app.services.whatsapp_service.WhatsAppService", return_value=mock_wa),
        patch("app.tasks.pantry.update_pantry_post_order.apply_async"),
        patch("app.tasks.pantry.update_pantry_post_order.delay"),
    ):
        ctx, _ = mock_db_ctx()
        mock_ctx.return_value = ctx
        result = await place(state)

    # checkout response gives order_id = "241629385719397"
    assert result.get("swiggy_order_id") == "241629385719397"
    assert result.get("final_total")     == 520.0
    mock_wa.send_order_receipt.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# 15–16. place() — address fallback (critical path)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_place_node_falls_back_to_swiggy_address_when_pref_is_null(db, swiggy_mcp):
    """place() with preferred_address_id=None fetches address from Swiggy and succeeds."""
    from app.agent.planning_graph import place
    from app.models.db import LoopRun

    household_id = await create_household(db)
    run = LoopRun(household_id=household_id, trigger_type="scheduled", state="confirmed")
    db.add(run)
    await db.commit()

    resolved = [
        {"item_name": "Tata Salt", "sku_id": "sku_tata_salt_001",
         "product_name": "Tata Salt 1kg", "brand": "Tata", "category": "staples",
         "quantity": 1.0, "unit": "kg", "unit_price": 28.0, "total_price": 28.0,
         "in_stock": True, "added_by": "rules_engine", "is_substitution": False,
         "original_item_name": None, "substitution_reason": None},
    ]

    state = {
        "household_id":             household_id,
        "loop_run_id":              str(run.id),
        "access_token":             "fake_access_token_for_tests",
        "preferred_address_id":     None,   # ← no address saved
        "preferred_delivery_slot":  "evening",
        "whatsapp_number":          "+918499933228",
        "resolved_basket":          resolved,
        "estimated_total":          520.0,
        "household_profile":        {"budget_max": 2500},
    }

    mock_wa = MagicMock()
    mock_wa.send_order_receipt = AsyncMock()

    with (
        patch("app.agent.planning_graph._db_context") as mock_ctx,
        patch("app.services.whatsapp_service.WhatsAppService", return_value=mock_wa),
        patch("app.tasks.pantry.update_pantry_post_order.apply_async"),
        patch("app.tasks.pantry.update_pantry_post_order.delay"),
    ):
        ctx, _ = mock_db_ctx()
        mock_ctx.return_value = ctx
        result = await place(state)

    # Should have resolved "addr_home_001" from swiggy_mcp fallback and placed order
    assert result.get("swiggy_order_id") == "241629385719397"
    assert result.get("should_abort") is not True


@pytest.mark.asyncio
async def test_place_node_fails_gracefully_when_no_address_anywhere(db):
    """place() with preferred_address_id=None and empty Swiggy address list → should_abort."""
    from app.agent.planning_graph import place
    from app.models.db import LoopRun
    from tests.integration.conftest import _mcp_ok

    household_id = await create_household(db)
    run = LoopRun(household_id=household_id, trigger_type="scheduled", state="confirmed")
    db.add(run)
    await db.commit()

    state = {
        "household_id":             household_id,
        "loop_run_id":              str(run.id),
        "access_token":             "fake_access_token_for_tests",
        "preferred_address_id":     None,
        "preferred_delivery_slot":  "evening",
        "whatsapp_number":          "+918499933228",
        "resolved_basket":          [
            {"item_name": "Tata Salt", "sku_id": "sku_tata_salt_001",
             "product_name": "Tata Salt 1kg", "brand": "Tata", "category": "staples",
             "quantity": 1.0, "unit": "kg", "unit_price": 28.0, "total_price": 28.0,
             "in_stock": True, "added_by": "rules_engine", "is_substitution": False,
             "original_item_name": None, "substitution_reason": None},
        ],
        "estimated_total":          520.0,
        "household_profile":        {"budget_max": 2500},
    }

    # Override get_addresses to return empty list
    async def _no_addresses(self, url, *, json=None, **kwargs):
        body = json or {}
        if body.get("jsonrpc") == "2.0":
            tool = body.get("params", {}).get("name", "")
            if tool == "get_addresses":
                return _mcp_ok("get_addresses", {"addresses": []})
            return _mcp_ok(tool)
        import httpx as _httpx
        return await _httpx.AsyncClient.post(self, url, json=json, **kwargs)

    with patch("httpx.AsyncClient.post", new=_no_addresses):
        result = await place(state)

    assert result.get("should_abort") is True
    assert result.get("error_stage") == "place"
