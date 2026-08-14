"""
Integration tests — assistant tool dispatch (app/services/assistant_tools.py)
tasks/features/ai-ordering-assistant.md, Design §3.

Each write-tool handler, called directly with valid arguments, must produce
the same DB state as calling the underlying route/service directly — and
malformed/incomplete arguments must fail Pydantic validation before
touching the database, not after.
"""

import pytest

from app.services import assistant_tools, quick_basket
from tests.integration.conftest import create_household


@pytest.mark.asyncio
async def test_get_basket_read_tool(db):
    household_id = await create_household(db)
    await quick_basket.add_item(household_id, {
        "item_name": "Tata Salt", "sku_id": "sku_1", "unit_price": 28.0, "quantity": 1,
    })

    result = await assistant_tools.execute_tool("get_basket", {}, household_id, db)
    assert len(result["items"]) == 1
    assert result["items"][0]["item_name"] == "Tata Salt"


@pytest.mark.asyncio
async def test_list_routines_read_tool_empty(db):
    household_id = await create_household(db)
    result = await assistant_tools.execute_tool("list_routines", {}, household_id, db)
    assert result["routines"] == []


@pytest.mark.asyncio
async def test_create_routine_valid_args_creates_real_routine(db):
    household_id = await create_household(db)
    tool_input = {
        "name": "Weekly Milk", "frequency_type": "weekly", "frequency_value": 1,
        "schedule_time": "09:00",
        "items": [{"item_name": "Amul Toned Milk", "quantity": 1, "unit": "unit"}],
    }
    result = await assistant_tools.execute_tool("create_routine", tool_input, household_id, db)

    assert "error" not in result
    assert result["routine"]["name"] == "Weekly Milk"
    assert result["routine"]["frequency_type"] == "weekly"

    from app.services.routines_service import RoutinesService
    routines = await RoutinesService(db).list_routines(household_id)
    assert len(routines) == 1
    assert routines[0].name == "Weekly Milk"


@pytest.mark.asyncio
async def test_create_routine_missing_required_field_fails_validation_before_db_write(db):
    household_id = await create_household(db)
    # Missing frequency_value, schedule_time, items — LLM extracted an
    # incomplete request. Must fail Pydantic validation, not reach the DB.
    tool_input = {"name": "Weekly Milk", "frequency_type": "weekly"}
    result = await assistant_tools.execute_tool("create_routine", tool_input, household_id, db)

    assert result["error"] == "validation_failed"

    from app.services.routines_service import RoutinesService
    routines = await RoutinesService(db).list_routines(household_id)
    assert routines == []


@pytest.mark.asyncio
async def test_create_routine_invalid_frequency_type_fails_validation(db):
    household_id = await create_household(db)
    tool_input = {
        "name": "X", "frequency_type": "hourly",  # not a valid enum value
        "frequency_value": 1, "schedule_time": "09:00",
        "items": [{"item_name": "Milk"}],
    }
    result = await assistant_tools.execute_tool("create_routine", tool_input, household_id, db)
    assert result["error"] == "validation_failed"


@pytest.mark.asyncio
async def test_edit_routine_patches_existing_routine(db):
    from app.schemas.routines import RoutineCreate
    from app.services.routines_service import RoutinesService

    household_id = await create_household(db)
    created = await RoutinesService(db).create(household_id, RoutineCreate(
        name="Weekly Milk", frequency_type="weekly", frequency_value=1,
        schedule_time="09:00", items=[{"item_name": "Milk", "quantity": 1}],
    ))

    result = await assistant_tools.execute_tool(
        "edit_routine", {"routine_id": created.id, "name": "Weekly Milk & Curd"}, household_id, db,
    )

    assert "error" not in result
    assert result["routine"]["name"] == "Weekly Milk & Curd"


@pytest.mark.asyncio
async def test_edit_routine_missing_routine_id_fails_validation(db):
    household_id = await create_household(db)
    result = await assistant_tools.execute_tool("edit_routine", {"name": "X"}, household_id, db)
    assert result["error"] == "validation_failed"


@pytest.mark.asyncio
async def test_pause_and_resume_routine(db):
    from app.schemas.routines import RoutineCreate
    from app.services.routines_service import RoutinesService

    household_id = await create_household(db)
    created = await RoutinesService(db).create(household_id, RoutineCreate(
        name="Weekly Milk", frequency_type="weekly", frequency_value=1,
        schedule_time="09:00", items=[{"item_name": "Milk", "quantity": 1}],
    ))

    paused = await assistant_tools.execute_tool("pause_routine", {"routine_id": created.id}, household_id, db)
    assert paused["routine"]["status"] == "paused"

    resumed = await assistant_tools.execute_tool("resume_routine", {"routine_id": created.id}, household_id, db)
    assert resumed["routine"]["status"] == "active"


@pytest.mark.asyncio
async def test_delete_routine(db):
    from app.schemas.routines import RoutineCreate
    from app.services.routines_service import RoutinesService

    household_id = await create_household(db)
    created = await RoutinesService(db).create(household_id, RoutineCreate(
        name="Weekly Milk", frequency_type="weekly", frequency_value=1,
        schedule_time="09:00", items=[{"item_name": "Milk", "quantity": 1}],
    ))

    result = await assistant_tools.execute_tool("delete_routine", {"routine_id": created.id}, household_id, db)
    assert result["deleted"] is True

    routines = await RoutinesService(db).list_routines(household_id)
    assert routines == []


@pytest.mark.asyncio
async def test_routine_action_unknown_id_returns_not_found(db):
    household_id = await create_household(db)
    result = await assistant_tools.execute_tool("pause_routine", {"routine_id": "does-not-exist"}, household_id, db)
    assert result["error"] == "not_found"


@pytest.mark.asyncio
async def test_search_and_add_to_basket(db, swiggy_mcp):
    # search_items is address-scoped (availability/pricing) — needs the same
    # stored-address setup checkout does.
    household_id = await create_household(db)
    await _give_household_a_stored_address(db, household_id)
    result = await assistant_tools.execute_tool(
        "search_and_add_to_basket", {"query": "salt", "quantity": 2}, household_id, db,
    )
    assert "error" not in result
    assert result["added"]["quantity"] == 2

    basket = await quick_basket.get_basket(household_id)
    assert len(basket) == 1


@pytest.mark.asyncio
async def test_remove_from_basket_by_name(db):
    household_id = await create_household(db)
    await quick_basket.add_item(household_id, {
        "item_name": "Tata Salt", "sku_id": "sku_1", "unit_price": 28.0, "quantity": 1,
    })

    result = await assistant_tools.execute_tool("remove_from_basket", {"item_name": "salt"}, household_id, db)
    assert "error" not in result
    assert result["removed"]["item_name"] == "Tata Salt"
    assert await quick_basket.get_basket(household_id) == []


@pytest.mark.asyncio
async def test_remove_from_basket_no_match_returns_not_found(db):
    household_id = await create_household(db)
    result = await assistant_tools.execute_tool("remove_from_basket", {"item_name": "nonexistent"}, household_id, db)
    assert result["error"] == "not_found"


async def _give_household_a_stored_address(db, household_id: str) -> None:
    from sqlalchemy import select, update
    from app.models.db import Address, HouseholdPreferences

    addr = Address(household_id=household_id, swiggy_address_id="addr_home_001", label="Home", is_default=True)
    db.add(addr)
    await db.flush()
    await db.execute(
        update(HouseholdPreferences)
        .where(HouseholdPreferences.household_id == household_id)
        .values(preferred_address_id=addr.id)
    )
    await db.commit()


@pytest.mark.asyncio
async def test_checkout_basket_tool_calls_same_service_as_route(db, swiggy_mcp):
    # checkout_basket's tool schema takes no address override — it relies on
    # the household's stored preferred address, same as the REST route's
    # default path. create_household() leaves that unset, so this test sets
    # it up explicitly rather than relying on an override the tool doesn't
    # expose (matches how a real onboarded household would already have
    # this configured).
    household_id = await create_household(db)
    await _give_household_a_stored_address(db, household_id)
    await quick_basket.add_item(household_id, {
        "item_name": "Tata Salt", "sku_id": "sku_tata_salt_001", "unit_price": 28.0, "quantity": 1,
    })

    result = await assistant_tools.execute_tool("checkout_basket", {}, household_id, db)
    assert result["success"] is True
    assert await quick_basket.get_basket(household_id) == []


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_not_exception(db):
    household_id = await create_household(db)
    result = await assistant_tools.execute_tool("delete_everything", {}, household_id, db)
    assert result["error"] == "unknown_tool"
