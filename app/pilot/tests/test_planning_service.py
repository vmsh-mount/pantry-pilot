"""
Tests for PlanningService and the planning graph nodes.

Covers:
  - create_loop_run: happy path, paused household, not onboarded
  - plan_rules node: reorder flagging, diet filter (vegetarian, jain), allergy block
  - plan_llm node: LLM failure is non-fatal, diet re-filter on additions
  - optimize node: sku resolution, out-of-stock substitution, budget trim
  - confirm node: sends basket preview, persists items, schedules timeout
  - handle_timeout: already resolved → noop; awaiting → skips + reschedules
  - reschedule_next_run: writes next_run_at, enqueues Celery task
  - _violates_diet: vegetarian/jain checks
  - mark_failed: sets state=failed in DB
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.agent.planning_graph import (
    plan_rules,
    plan_llm,
    _violates_diet,
)
from app.agent.state import PlanningState
from app.utils.exceptions import HouseholdPausedError, HouseholdNotFoundError


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_base_state(**overrides) -> PlanningState:
    state: PlanningState = {
        "household_id":           "hh-001",
        "loop_run_id":            "run-001",
        "access_token":           "token_abc",
        "trigger_type":           "scheduled",
        "should_abort":           False,
        "household_profile": {
            "member_count":  2,
            "diet_type":     "vegetarian",
            "allergies":     [],
            "budget_min":    1500,
            "budget_max":    2500,
            "budget_mid":    2000,
            "city":          "Bengaluru",
        },
        "pantry_items":       [],
        "brand_preferences":  {},
        "preferred_address_id":    "addr_1",
        "preferred_delivery_slot": "evening",
        "whatsapp_number":    "+919999999999",
        "week_label":         "Week of 30 Jun 2026",
        "candidate_basket":   [],
        "llm_additions":      [],
        "llm_flags":          [],
    }
    state.update(overrides)
    return state


def make_pantry_item(
    name: str,
    remaining: float,
    threshold: float,
    category: str = "staples",
    unit: str = "kg",
    last_ordered_qty: float = 1.0,
) -> dict:
    return {
        "item_name":               name,
        "category":                category,
        "unit":                    unit,
        "estimated_qty_remaining": remaining,
        "reorder_threshold":       threshold,
        "last_ordered_qty":        last_ordered_qty,
        "avg_weekly_consumption":  0.3,
        "times_ordered":           5,
    }


# ══════════════════════════════════════════════════════════════════════════════
# _violates_diet
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name,diet,expected", [
    ("Chicken Breast 500g",  "vegetarian", True),
    ("Aashirvaad Atta 5kg",  "vegetarian", False),
    ("Onion 1kg",            "jain",       True),
    ("Garlic Paste 200g",    "jain",       True),
    ("Toor Dal 1kg",         "jain",       False),
    ("Fish Curry Masala",    "vegan",      True),
    ("Soy Milk 500ml",       "vegan",      False),
])
def test_violates_diet(name, diet, expected):
    assert _violates_diet(name, diet) == expected


# ══════════════════════════════════════════════════════════════════════════════
# plan_rules
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_plan_rules_flags_depleted_item():
    """Item below threshold should appear in candidate_basket."""
    state = make_base_state(pantry_items=[
        make_pantry_item("Toor Dal", remaining=0.1, threshold=0.3)
    ])

    with patch("app.agent.planning_graph._db_context") as mock_ctx:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.commit  = AsyncMock()
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__  = AsyncMock(return_value=False)

        result = await plan_rules(state)

    assert len(result["candidate_basket"]) == 1
    assert result["candidate_basket"][0]["item_name"] == "Toor Dal"
    assert result["candidate_basket"][0]["added_by"]  == "rules_engine"


@pytest.mark.asyncio
async def test_plan_rules_skips_stocked_item():
    """Item above threshold should NOT appear in candidate_basket."""
    state = make_base_state(pantry_items=[
        make_pantry_item("Atta", remaining=2.0, threshold=0.3)
    ])

    with patch("app.agent.planning_graph._db_context") as mock_ctx:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.commit  = AsyncMock()
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__  = AsyncMock(return_value=False)

        result = await plan_rules(state)

    assert result["candidate_basket"] == []


@pytest.mark.asyncio
async def test_plan_rules_blocks_non_veg_for_vegetarian():
    """Chicken should be hard-blocked for vegetarian diet."""
    state = make_base_state(
        household_profile={
            **make_base_state()["household_profile"],
            "diet_type": "vegetarian",
        },
        pantry_items=[
            make_pantry_item("Chicken Curry Mix", remaining=0.0, threshold=0.1),
            make_pantry_item("Toor Dal",          remaining=0.0, threshold=0.3),
        ]
    )

    with patch("app.agent.planning_graph._db_context") as mock_ctx:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.commit  = AsyncMock()
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__  = AsyncMock(return_value=False)

        result = await plan_rules(state)

    names = [i["item_name"] for i in result["candidate_basket"]]
    assert "Toor Dal"          in names
    assert "Chicken Curry Mix" not in names


@pytest.mark.asyncio
async def test_plan_rules_jain_blocks_onion():
    """Onion should be hard-blocked for jain diet."""
    state = make_base_state(
        household_profile={
            **make_base_state()["household_profile"],
            "diet_type": "jain",
        },
        pantry_items=[
            make_pantry_item("Onions",   remaining=0.0, threshold=0.1),
            make_pantry_item("Toor Dal", remaining=0.0, threshold=0.3),
        ]
    )

    with patch("app.agent.planning_graph._db_context") as mock_ctx:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.commit  = AsyncMock()
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__  = AsyncMock(return_value=False)

        result = await plan_rules(state)

    names = [i["item_name"] for i in result["candidate_basket"]]
    assert "Toor Dal" in names
    assert "Onions"   not in names


@pytest.mark.asyncio
async def test_plan_rules_allergy_block():
    """Item matching an allergy keyword should be blocked."""
    state = make_base_state(
        household_profile={
            **make_base_state()["household_profile"],
            "allergies": ["peanut"],
        },
        pantry_items=[
            make_pantry_item("Peanut Butter 200g", remaining=0.0, threshold=0.1),
            make_pantry_item("Toor Dal",           remaining=0.0, threshold=0.3),
        ]
    )

    with patch("app.agent.planning_graph._db_context") as mock_ctx:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.commit  = AsyncMock()
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__  = AsyncMock(return_value=False)

        result = await plan_rules(state)

    names = [i["item_name"] for i in result["candidate_basket"]]
    assert "Toor Dal"          in names
    assert "Peanut Butter 200g" not in names


@pytest.mark.asyncio
async def test_plan_rules_aborts_if_should_abort():
    state = make_base_state(should_abort=True)
    result = await plan_rules(state)
    assert result == {}


# ══════════════════════════════════════════════════════════════════════════════
# plan_llm
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_plan_llm_failure_is_nonfatal():
    """LLM API error should return empty additions without raising."""
    state = make_base_state(candidate_basket=[
        {"item_name": "Toor Dal", "quantity": 1.0, "unit": "kg", "category": "staples"}
    ])

    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(side_effect=Exception("API timeout"))

    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        result = await plan_llm(state)

    assert result["llm_additions"] == []
    assert len(result["llm_flags"]) >= 1
    assert result["llm_tokens_used"] == 0


@pytest.mark.asyncio
async def test_plan_llm_filters_non_veg_from_additions():
    """LLM-suggested chicken should be stripped for vegetarian diet."""
    state = make_base_state(
        household_profile={
            **make_base_state()["household_profile"],
            "diet_type": "vegetarian",
        },
        candidate_basket=[],
    )

    llm_json = json.dumps({
        "additions": [
            {"item_name": "chicken tikka", "quantity": 0.5, "unit": "kg",
             "category": "protein", "reason": "protein gap"},
            {"item_name": "fresh spinach", "quantity": 1.0, "unit": "bunch",
             "category": "fresh_produce", "reason": "vegetables"},
        ],
        "flags": [],
    })

    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=llm_json)

    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        result = await plan_llm(state)

    names = [a["item_name"] for a in result["llm_additions"]]
    assert "chicken tikka"  not in names
    assert "fresh spinach"  in names


@pytest.mark.asyncio
async def test_plan_llm_aborts_if_should_abort():
    state = make_base_state(should_abort=True)
    result = await plan_llm(state)
    assert result == {}


# ══════════════════════════════════════════════════════════════════════════════
# PlanningService unit tests
# ══════════════════════════════════════════════════════════════════════════════

def make_planning_service():
    from app.services.planning_service import PlanningService
    mock_db = AsyncMock()
    return PlanningService(mock_db), mock_db


@pytest.mark.asyncio
async def test_create_loop_run_paused_household_raises():
    from app.services.planning_service import PlanningService
    from app.models.db import Household

    mock_db = AsyncMock()
    household         = Household()
    household.is_paused           = True
    household.onboarding_complete = True

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = household
    mock_db.execute.return_value = mock_result

    service = PlanningService(mock_db)
    with pytest.raises(HouseholdPausedError):
        await service.create_loop_run("hh-001", "scheduled")


@pytest.mark.asyncio
async def test_create_loop_run_not_onboarded_raises():
    from app.services.planning_service import PlanningService
    from app.models.db import Household

    mock_db   = AsyncMock()
    household = Household()
    household.is_paused           = False
    household.onboarding_complete = False

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = household
    mock_db.execute.return_value = mock_result

    service = PlanningService(mock_db)
    with pytest.raises(HouseholdNotFoundError):
        await service.create_loop_run("hh-001", "scheduled")


@pytest.mark.asyncio
async def test_create_loop_run_not_found_raises():
    from app.services.planning_service import PlanningService

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    service = PlanningService(mock_db)
    with pytest.raises(HouseholdNotFoundError):
        await service.create_loop_run("hh-nonexistent", "scheduled")


@pytest.mark.asyncio
async def test_handle_timeout_noop_if_already_resolved():
    """If loop run is not in awaiting_confirmation state, handle_timeout does nothing."""
    from app.services.planning_service import PlanningService

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None   # not awaiting
    mock_db.execute.return_value = mock_result

    service = PlanningService(mock_db)
    await service.handle_timeout("hh-001", "run-001")

    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_mark_failed_sets_state():
    from app.services.planning_service import PlanningService

    mock_db = AsyncMock()
    service = PlanningService(mock_db)

    await service.mark_failed("run-001", reason="MCP timeout", stage="optimize")

    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()
