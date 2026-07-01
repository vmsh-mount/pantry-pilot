"""
Integration tests — Planning Graph (sense → plan_rules → plan_llm → optimize → confirm)

Each test drives one complete flow through the LangGraph nodes using:
  - Real node functions (not mocked)
  - Swiggy MCP mocked at the HTTP level via `swiggy_mcp` fixture (session scope)
  - In-process SQLite DB via `db` fixture (per-test)

Flows covered:
  1.  sense — fresh pantry → falls back to your_go_to_items
  2.  sense — established pantry → reads pantry items
  3.  plan_rules — depleted items flagged for reorder
  4.  plan_rules — vegetarian diet blocks meat
  5.  plan_rules — jain diet blocks onion / garlic / potato
  6.  plan_rules — allergy keyword blocks item
  7.  plan_rules — go_to_items path (no pantry)
  8.  plan_llm — LLM failure is non-fatal, basket continues
  9.  plan_llm — LLM additions pass diet filter
  10. optimize — resolves SKU, captures price
  11. optimize — out-of-stock → substitution attempted
  12. optimize — all unavailable → empty resolved basket
  13. optimize — budget exceeded → trims low-priority items
  14. confirm — empty basket → clean skip (no WhatsApp)
  15. confirm — non-empty → WhatsApp sent, state=awaiting_confirmation
  16. Full pipeline — fresh user, go_to_items bootstrap
  17. Full pipeline — established user, pantry decay triggers reorder
"""

import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.agent.planning_graph import sense, plan_rules, plan_llm, optimize, confirm
from app.agent.state import PlanningState
from tests.integration.conftest import (
    SWIGGY_RESPONSES, _mcp_ok, _mcp_error, create_household,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def base_state(**overrides) -> PlanningState:
    s: PlanningState = {
        "household_id":            "00000000-0000-0000-0000-000000000001",
        "loop_run_id":             "00000000-0000-0000-0000-000000000002",
        "access_token":            "fake_access_token_for_tests",
        "trigger_type":            "scheduled",
        "should_abort":            False,
        "household_profile": {
            "member_count":  2,
            "diet_type":     "vegetarian",
            "allergies":     [],
            "budget_min":    1500,
            "budget_max":    2500,
            "budget_mid":    2000,
            "city":          "Bengaluru",
        },
        "pantry_items":            [],
        "recent_orders":           [],
        "brand_preferences":       {},
        "preferred_address_id":    "addr_home_001",
        "preferred_delivery_slot": "evening",
        "whatsapp_number":         "+918499933228",
        "week_label":              "Week of 28 Jun 2026",
        "candidate_basket":        [],
        "llm_additions":           [],
        "llm_flags":               [],
    }
    s.update(overrides)
    return s


def pantry_item(name, remaining, threshold, category="staples", unit="kg", last_ordered=1.0):
    return {
        "item_name":               name,
        "category":                category,
        "unit":                    unit,
        "estimated_qty_remaining": remaining,
        "reorder_threshold":       threshold,
        "last_ordered_qty":        last_ordered,
        "avg_weekly_consumption":  0.3,
        "times_ordered":           5,
    }


def mock_db_ctx():
    """Context-manager mock for _db_context() used inside graph nodes."""
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock())
    mock_db.commit  = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__  = AsyncMock(return_value=False)
    return ctx, mock_db


# ══════════════════════════════════════════════════════════════════════════════
# 1. SENSE — fresh pantry falls back to your_go_to_items
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sense_fresh_pantry_uses_go_to_items(db, swiggy_mcp):
    """First-run user with empty pantry: sense should call your_go_to_items and
    populate recent_orders with the returned items."""
    household_id = await create_household(db)

    with patch("app.database.AsyncSessionLocal", return_value=db):
        result = await sense(base_state(household_id=household_id))

    assert result["should_abort"] is False
    assert result["pantry_items"] == []
    # go_to_items fallback should have populated recent_orders
    assert len(result["recent_orders"]) > 0
    first = result["recent_orders"][0]
    assert "item_name"  in first
    assert "sku_id"     in first
    assert "unit_price" in first


@pytest.mark.asyncio
async def test_sense_established_pantry_skips_go_to_items(db, swiggy_mcp):
    """User with existing pantry: sense reads from DB, no go_to_items call."""
    from app.models.db import PantryItem
    household_id = await create_household(db)

    # Seed a pantry item
    db.add(PantryItem(
        household_id          = household_id,
        item_name             = "Toor Dal",
        category              = "staples",
        standard_unit         = "kg",
        estimated_qty_remaining = 0.1,
        reorder_threshold     = 0.5,
    ))
    await db.commit()

    call_log: list[str] = []

    orig_post = __import__("httpx").AsyncClient.post

    async def _spy_post(self, url, *, json=None, **kwargs):
        tool = (json or {}).get("params", {}).get("name", "")
        call_log.append(tool)
        return _mcp_ok(tool)

    with (
        patch("app.database.AsyncSessionLocal", return_value=db),
        patch("httpx.AsyncClient.post", new=_spy_post),
    ):
        result = await sense(base_state(household_id=household_id))

    assert len(result["pantry_items"]) == 1
    assert result["pantry_items"][0]["item_name"] == "Toor Dal"
    # your_go_to_items should NOT have been called
    assert "your_go_to_items" not in call_log


# ══════════════════════════════════════════════════════════════════════════════
# 2–7. PLAN_RULES
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_plan_rules_depleted_item_added():
    """Item below reorder threshold → added to candidate basket."""
    state = base_state(pantry_items=[
        pantry_item("Toor Dal", remaining=0.1, threshold=0.5),
    ])
    with patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]):
        result = await plan_rules(state)

    assert len(result["candidate_basket"]) == 1
    assert result["candidate_basket"][0]["item_name"] == "Toor Dal"
    assert result["candidate_basket"][0]["added_by"]  == "rules_engine"


@pytest.mark.asyncio
async def test_plan_rules_stocked_item_not_added():
    """Item above threshold → NOT in candidate basket."""
    state = base_state(pantry_items=[
        pantry_item("Atta", remaining=3.0, threshold=1.0),
    ])
    with patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]):
        result = await plan_rules(state)

    assert result["candidate_basket"] == []


@pytest.mark.asyncio
async def test_plan_rules_vegetarian_blocks_chicken():
    """Chicken blocked for vegetarian diet; dal passes through."""
    state = base_state(
        household_profile={**base_state()["household_profile"], "diet_type": "vegetarian"},
        pantry_items=[
            pantry_item("Chicken 500g", remaining=0.0, threshold=0.1),
            pantry_item("Toor Dal",     remaining=0.0, threshold=0.3),
        ],
    )
    with patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]):
        result = await plan_rules(state)

    names = [i["item_name"] for i in result["candidate_basket"]]
    assert "Toor Dal"    in names
    assert "Chicken 500g" not in names


@pytest.mark.asyncio
async def test_plan_rules_jain_blocks_onion_garlic_potato():
    """Onion, Garlic, Potato blocked for jain diet."""
    state = base_state(
        household_profile={**base_state()["household_profile"], "diet_type": "jain"},
        pantry_items=[
            pantry_item("Onions",   remaining=0.0, threshold=0.1),
            pantry_item("Garlic",   remaining=0.0, threshold=0.05),
            pantry_item("Potatoes", remaining=0.0, threshold=0.3),
            pantry_item("Toor Dal", remaining=0.0, threshold=0.3),
        ],
    )
    with patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]):
        result = await plan_rules(state)

    names = [i["item_name"] for i in result["candidate_basket"]]
    assert "Toor Dal" in names
    for blocked in ("Onions", "Garlic", "Potatoes"):
        assert blocked not in names


@pytest.mark.asyncio
async def test_plan_rules_allergy_blocks_item():
    """Allergy keyword match → item blocked."""
    state = base_state(
        household_profile={**base_state()["household_profile"], "allergies": ["peanut"]},
        pantry_items=[
            pantry_item("Peanut Butter 200g", remaining=0.0, threshold=0.1),
            pantry_item("Toor Dal",           remaining=0.0, threshold=0.3),
        ],
    )
    with patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]):
        result = await plan_rules(state)

    names = [i["item_name"] for i in result["candidate_basket"]]
    assert "Toor Dal"           in names
    assert "Peanut Butter 200g" not in names


@pytest.mark.asyncio
async def test_plan_rules_go_to_items_path():
    """No pantry + recent_orders → go_to_items added as candidate with sku_id."""
    state = base_state(
        pantry_items=[],
        recent_orders=[
            {"item_name": "Tata Salt", "sku_id": "sku_001", "brand": "Tata",
             "category": "staples", "unit": "1kg", "qty": 1, "unit_price": 28.0, "order_count": 5},
        ],
    )
    with patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]):
        result = await plan_rules(state)

    assert len(result["candidate_basket"]) == 1
    c = result["candidate_basket"][0]
    assert c["item_name"] == "Tata Salt"
    assert c["sku_id"]    == "sku_001"
    assert c["added_by"]  == "go_to_items"


@pytest.mark.asyncio
async def test_plan_rules_aborts_early():
    state = base_state(should_abort=True)
    result = await plan_rules(state)
    assert result == {}


# ══════════════════════════════════════════════════════════════════════════════
# 8–9. PLAN_LLM
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_plan_llm_failure_non_fatal():
    """LLM exception → empty additions returned, no crash."""
    state = base_state(candidate_basket=[
        {"item_name": "Toor Dal", "quantity": 1.0, "unit": "kg", "category": "staples"},
    ])

    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(side_effect=Exception("Anthropic API down"))

    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        result = await plan_llm(state)

    assert result["llm_additions"] == []
    assert any("unavailable" in f.lower() or "llm" in f.lower() for f in result["llm_flags"])


@pytest.mark.asyncio
async def test_plan_llm_additions_filtered_by_diet():
    """LLM suggests chicken for vegetarian household → stripped from additions."""
    state = base_state(
        household_profile={**base_state()["household_profile"], "diet_type": "vegetarian"},
        candidate_basket=[],
    )

    llm_response = json.dumps({
        "additions": [
            {"item_name": "chicken curry", "quantity": 0.5, "unit": "kg",
             "category": "protein", "reason": "protein"},
            {"item_name": "fresh spinach", "quantity": 1.0, "unit": "bunch",
             "category": "fresh_produce", "reason": "vegetables"},
        ],
        "flags": [],
    })
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=llm_response)

    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        result = await plan_llm(state)

    names = [a["item_name"] for a in result["llm_additions"]]
    assert "fresh spinach" in names
    assert "chicken curry" not in names


@pytest.mark.asyncio
async def test_plan_llm_seasonal_note_in_flags():
    """Seasonal note from LLM should appear in llm_flags."""
    state = base_state(candidate_basket=[])
    llm_response = json.dumps({
        "additions": [],
        "flags": ["Budget looks tight."],
        "seasonal_note": "July is peak mango season in South India.",
    })
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=llm_response)

    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        result = await plan_llm(state)

    assert any("mango" in f for f in result["llm_flags"])


# ══════════════════════════════════════════════════════════════════════════════
# 10–13. OPTIMIZE
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_optimize_resolves_sku_and_price(swiggy_mcp):
    """Optimize calls search_products and captures SKU + price."""
    state = base_state(
        candidate_basket=[
            {"item_name": "Tata Salt", "sku_id": None, "quantity": 1.0,
             "unit": "kg", "category": "staples", "brand": None,
             "added_by": "rules_engine", "is_substitution": False},
        ],
        llm_additions=[],
    )
    with patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]):
        result = await optimize(state)

    assert len(result["resolved_basket"]) == 1
    item = result["resolved_basket"][0]
    assert item["sku_id"]     == "sku_tata_salt_001"
    assert item["unit_price"] == 28.0
    assert item["total_price"] == 28.0
    assert result["estimated_total"] == 28.0


@pytest.mark.asyncio
async def test_optimize_all_unavailable_empty_basket(swiggy_mcp):
    """All search_products calls return empty → resolved basket is empty."""
    swiggy_mcp["search_products"] = _mcp_ok("search_products", {"products": [], "totalCount": 0})

    state = base_state(
        candidate_basket=[
            {"item_name": "Exotic Truffle Oil", "sku_id": None, "quantity": 1.0,
             "unit": "bottle", "category": "staples", "brand": None,
             "added_by": "rules_engine", "is_substitution": False},
        ],
        llm_additions=[],
    )
    with patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]):
        result = await optimize(state)

    assert result["resolved_basket"] == []
    assert "Exotic Truffle Oil" in result["items_unavailable"]

    # cleanup
    del swiggy_mcp["search_products"]


@pytest.mark.asyncio
async def test_optimize_budget_trim(swiggy_mcp):
    """When estimated total > budget_max, low-priority items are trimmed."""
    # Two packaged items totalling more than the ₹100 budget we'll set
    state = base_state(
        household_profile={**base_state()["household_profile"], "budget_max": 100},
        candidate_basket=[
            {"item_name": "Britannia Biscuits", "sku_id": None, "quantity": 5.0,
             "unit": "pack", "category": "packaged", "brand": None,
             "added_by": "rules_engine", "is_substitution": False},
            {"item_name": "Tata Salt", "sku_id": None, "quantity": 1.0,
             "unit": "kg", "category": "staples", "brand": None,
             "added_by": "rules_engine", "is_substitution": False},
        ],
        llm_additions=[],
    )
    # Make search return price=80 for all queries
    swiggy_mcp["search_products"] = _mcp_ok("search_products", {
        "products": [{"itemId": "sku_x", "name": "Product", "brand": "Brand",
                      "category": "packaged", "unit": "pcs", "price": 80.0, "inStock": True}],
        "totalCount": 1,
    })

    with patch("app.agent.planning_graph._db_context", return_value=mock_db_ctx()[0]):
        result = await optimize(state)

    # Total was 80*5 + 80*1 = 480 > 100; packaged trimmed first
    assert result["estimated_total"] <= 100
    del swiggy_mcp["search_products"]


@pytest.mark.asyncio
async def test_optimize_no_address_aborts():
    """Missing address ID with no fallback → should_abort=True."""
    state = base_state(
        preferred_address_id=None,
        candidate_basket=[
            {"item_name": "Toor Dal", "sku_id": None, "quantity": 1.0,
             "unit": "kg", "category": "staples", "brand": None,
             "added_by": "rules_engine", "is_substitution": False},
        ],
    )
    # Make get_addresses return empty so fallback also fails
    swiggy_mcp_local = {"get_addresses": _mcp_ok("get_addresses", {"addresses": []})}

    async def _empty_addresses(self, url, *, json=None, **kwargs):
        tool = (json or {}).get("params", {}).get("name", "")
        if tool in swiggy_mcp_local:
            return swiggy_mcp_local[tool]
        return _mcp_ok(tool)

    with patch("httpx.AsyncClient.post", new=_empty_addresses):
        result = await optimize(state)

    assert result.get("should_abort") is True
    assert result.get("error_stage") == "optimize"


# ══════════════════════════════════════════════════════════════════════════════
# 14–15. CONFIRM
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_confirm_empty_basket_skips_cleanly(db):
    """Empty resolved basket → state=skipped in DB, should_abort=True, no WhatsApp."""
    household_id = await create_household(db)
    from app.models.db import LoopRun
    loop_run = LoopRun(household_id=household_id, trigger_type="scheduled", state="optimizing")
    db.add(loop_run)
    await db.commit()

    mock_wa = AsyncMock()
    state = base_state(
        household_id  = household_id,
        loop_run_id   = str(loop_run.id),
        resolved_basket = [],
        whatsapp_number = "+918499933228",
    )

    with (
        patch("app.database.AsyncSessionLocal", return_value=db),
        patch("app.agent.planning_graph._db_context") as mock_ctx,
        patch("app.services.whatsapp_service.WhatsAppService", return_value=mock_wa),
    ):
        ctx, inner_db = mock_db_ctx()
        mock_ctx.return_value = ctx
        result = await confirm(state)

    assert result.get("should_abort") is True
    mock_wa.send_basket_preview.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_sends_whatsapp_for_non_empty_basket(db, swiggy_mcp):
    """Non-empty basket → WhatsApp send called, state=awaiting_confirmation."""
    household_id = await create_household(db)
    from app.models.db import LoopRun
    loop_run = LoopRun(household_id=household_id, trigger_type="scheduled", state="optimizing")
    db.add(loop_run)
    await db.commit()

    mock_wa = MagicMock()
    mock_wa.send_basket_preview = AsyncMock()

    resolved = [
        {"item_name": "Tata Salt", "sku_id": "sku_001", "product_name": "Tata Salt",
         "brand": "Tata", "category": "staples", "quantity": 1.0, "unit": "kg",
         "unit_price": 28.0, "total_price": 28.0, "in_stock": True,
         "added_by": "rules_engine", "add_reason": None,
         "is_substitution": False, "original_item_name": None, "substitution_reason": None},
    ]

    state = base_state(
        household_id      = household_id,
        loop_run_id       = str(loop_run.id),
        resolved_basket   = resolved,
        estimated_total   = 28.0,
        whatsapp_number   = "+918499933228",
    )

    with (
        patch("app.agent.planning_graph._db_context") as mock_ctx,
        patch("app.services.whatsapp_service.WhatsAppService", return_value=mock_wa),
        patch("app.tasks.planning.handle_confirmation_timeout.apply_async"),
    ):
        ctx, _ = mock_db_ctx()
        mock_ctx.return_value = ctx
        result = await confirm(state)

    assert result.get("should_abort") is not True
    mock_wa.send_basket_preview.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# 16–17. FULL PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_full_pipeline_fresh_user(db, swiggy_mcp):
    """
    End-to-end: fresh user with no pantry.
    go_to_items → plan_rules → plan_llm → optimize → confirm (WhatsApp sent).
    """
    from app.services.planning_service import PlanningService
    from app.models.db import LoopRun

    household_id = await create_household(db)
    loop_run = LoopRun(household_id=household_id, trigger_type="scheduled", state="pending")
    db.add(loop_run)
    await db.commit()

    mock_wa = MagicMock()
    mock_wa.send_basket_preview = AsyncMock()
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=json.dumps(
        {"additions": [], "flags": [], "seasonal_note": None}
    ))

    with (
        patch("app.database.AsyncSessionLocal", return_value=db),
        patch("app.agent.planning_graph._db_context") as mock_ctx,
        patch("app.providers.factory.get_llm_provider",      return_value=mock_llm),
        patch("app.services.whatsapp_service.WhatsAppService", return_value=mock_wa),
        patch("app.tasks.planning.handle_confirmation_timeout.apply_async"),
        patch("app.services.auth_service.AuthService.get_valid_token",
              new=AsyncMock(return_value="fake_access_token_for_tests")),
    ):
        ctx, _ = mock_db_ctx()
        mock_ctx.return_value = ctx
        svc = PlanningService(db)
        final_state = await svc.run_loop(household_id, str(loop_run.id))

    # Should have resolved at least some go_to_items into a basket
    assert not final_state.get("should_abort") or not final_state.get("error_stage")
    # WhatsApp called if basket was non-empty
    # (go_to_items returns 5 items from SWIGGY_RESPONSES, all resolvable)
    mock_wa.send_basket_preview.assert_called_once()


@pytest.mark.asyncio
async def test_full_pipeline_established_user_pantry_reorder(db, swiggy_mcp):
    """
    End-to-end: user with pantry items below threshold.
    Items should be resolved and WhatsApp confirmation sent.
    """
    from app.services.planning_service import PlanningService
    from app.models.db import LoopRun, PantryItem

    household_id = await create_household(db)

    db.add(PantryItem(
        household_id          = household_id,
        item_name             = "Toor Dal",
        category              = "staples",
        standard_unit         = "kg",
        estimated_qty_remaining = 0.1,
        reorder_threshold     = 0.5,
        last_ordered_qty      = 1.0,
    ))
    loop_run = LoopRun(household_id=household_id, trigger_type="scheduled", state="pending")
    db.add(loop_run)
    await db.commit()

    mock_wa = MagicMock()
    mock_wa.send_basket_preview = AsyncMock()
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=json.dumps(
        {"additions": [], "flags": [], "seasonal_note": None}
    ))

    with (
        patch("app.database.AsyncSessionLocal", return_value=db),
        patch("app.agent.planning_graph._db_context") as mock_ctx,
        patch("app.providers.factory.get_llm_provider",      return_value=mock_llm),
        patch("app.services.whatsapp_service.WhatsAppService", return_value=mock_wa),
        patch("app.tasks.planning.handle_confirmation_timeout.apply_async"),
        patch("app.services.auth_service.AuthService.get_valid_token",
              new=AsyncMock(return_value="fake_access_token_for_tests")),
    ):
        ctx, _ = mock_db_ctx()
        mock_ctx.return_value = ctx
        svc = PlanningService(db)
        final_state = await svc.run_loop(household_id, str(loop_run.id))

    # Toor Dal was below threshold, should appear in resolved_basket
    resolved_names = [i["item_name"] for i in final_state.get("resolved_basket", [])]
    assert "Toor Dal" in resolved_names
    mock_wa.send_basket_preview.assert_called_once()
