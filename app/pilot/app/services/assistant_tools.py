"""
AI ordering assistant — tool definitions + dispatch.
tasks/features/ai-ordering-assistant.md, Design §3.

Every handler here calls an existing service function — RoutinesService,
quick_basket, quick_checkout, or the nutrition query/gap functions — never
reimplements domain logic. LLM-extracted arguments are validated through
the same Pydantic schemas the REST routes already use (RoutineCreate,
RoutinePatch) before touching the database — malformed model output fails
validation the same way a malformed API request would, for free.

WRITE_TOOLS lists every tool that mutates state — the message endpoint
(Design §2) uses this to decide which tool calls must be proposed and
confirmed rather than executed inline, per the Non-Negotiable Constraint
in the PRD ("Confirm, don't decide").
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

WRITE_TOOLS = frozenset({
    "create_routine", "edit_routine", "pause_routine", "resume_routine",
    "delete_routine", "search_and_add_to_basket", "remove_from_basket",
    "checkout_basket",
})

READ_TOOLS = frozenset({
    "get_weekly_nutrition", "get_nutrition_gaps", "list_routines", "get_basket",
})


# ── Tool schemas (Anthropic format) ─────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "get_weekly_nutrition",
        "description": "Get the household's nutrition totals for the trailing N weeks, compared against personalised targets. Use for questions like 'how's my protein this week' or 'am I hitting my calorie target'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "weeks": {"type": "integer", "description": "How many trailing weeks to include (1-12)", "default": 4},
            },
        },
    },
    {
        "name": "get_nutrition_gaps",
        "description": "Get this week's nutrient shortfalls vs. targets (calories, protein, fiber, and diet-specific watch nutrients like B12/iron). Use for questions like 'what am I missing this week' or 'any nutrition gaps'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_routines",
        "description": "List the household's existing Routines (recurring scheduled orders).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_basket",
        "description": "Get the current Quick Order basket contents.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_routine",
        "description": "Propose creating a new recurring Routine. This authorizes every future order it will place on schedule — always propose for confirmation, never treat as read-only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "frequency_type": {"type": "string", "enum": ["every_n_days", "weekly", "monthly"]},
                "frequency_value": {"type": "integer", "description": "N days | 0-6 weekday (weekly) | 1-28 day-of-month (monthly)"},
                "schedule_time": {"type": "string", "description": "HH:MM in IST, e.g. '09:00'"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string"},
                            "quantity": {"type": "number", "default": 1},
                            "unit": {"type": "string", "default": "unit"},
                        },
                        "required": ["item_name"],
                    },
                },
            },
            "required": ["name", "frequency_type", "frequency_value", "schedule_time", "items"],
        },
    },
    {
        "name": "edit_routine",
        "description": "Propose changes to an existing Routine (name, schedule, or items). Only include fields that are changing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "routine_id": {"type": "string"},
                "name": {"type": "string"},
                "frequency_type": {"type": "string", "enum": ["every_n_days", "weekly", "monthly"]},
                "frequency_value": {"type": "integer"},
                "schedule_time": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit": {"type": "string"},
                        },
                        "required": ["item_name"],
                    },
                },
            },
            "required": ["routine_id"],
        },
    },
    {
        "name": "pause_routine",
        "description": "Propose pausing an active Routine (stops it firing until resumed).",
        "input_schema": {"type": "object", "properties": {"routine_id": {"type": "string"}}, "required": ["routine_id"]},
    },
    {
        "name": "resume_routine",
        "description": "Propose resuming a paused Routine.",
        "input_schema": {"type": "object", "properties": {"routine_id": {"type": "string"}}, "required": ["routine_id"]},
    },
    {
        "name": "delete_routine",
        "description": "Propose permanently deleting a Routine.",
        "input_schema": {"type": "object", "properties": {"routine_id": {"type": "string"}}, "required": ["routine_id"]},
    },
    {
        "name": "search_and_add_to_basket",
        "description": "Propose searching the Instamart catalog and adding the best-matching product to the Quick Order basket.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for, e.g. 'milk', 'eggs'"},
                "quantity": {"type": "integer", "default": 1},
            },
            "required": ["query"],
        },
    },
    {
        "name": "remove_from_basket",
        "description": "Propose removing an item from the current Quick Order basket by name.",
        "input_schema": {
            "type": "object",
            "properties": {"item_name": {"type": "string", "description": "Name (or partial name) of the basket item to remove"}},
            "required": ["item_name"],
        },
    },
    {
        "name": "checkout_basket",
        "description": "Propose placing the order for everything currently in the Quick Order basket. Highest-stakes tool — real money, real delivery.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _validate_routine_id(tool_input: dict) -> tuple[str | None, dict | None]:
    """(routine_id, None) on success, (None, error_dict) otherwise.

    RoutinesService's lookups filter by Routine.id, a UUID column — every
    other caller (the REST route's path param, an internal list_routines
    result) is already a real UUID by construction. The assistant is a new
    kind of caller: routine_id comes from whatever the model extracted from
    conversation, which could be hallucinated or mangled. Without this
    check, a non-UUID string reaches the database and raises a raw
    asyncpg.DataError instead of a graceful not-found — caught in review
    via a deliberately-malformed test id, not found any other way.
    """
    routine_id = tool_input.get("routine_id")
    if not routine_id:
        return None, {"error": "validation_failed", "details": "routine_id is required"}
    try:
        uuid.UUID(str(routine_id))
    except ValueError:
        return None, {"error": "not_found", "details": f"'{routine_id}' isn't a valid routine id"}
    return routine_id, None


# ── Handlers ─────────────────────────────────────────────────────────────────

async def _get_weekly_nutrition(tool_input: dict, household_id: str, db: AsyncSession) -> dict:
    from app.api.nutrition import get_weekly_nutrition_summary
    weeks = int(tool_input.get("weeks") or 4)
    weeks = max(1, min(12, weeks))
    return await get_weekly_nutrition_summary(db, household_id, weeks)


async def _get_nutrition_gaps(tool_input: dict, household_id: str, db: AsyncSession) -> dict:
    from sqlalchemy import select
    from app.models.db import Household
    from app.services.nutrition_gaps import compute_gaps

    hh = (await db.execute(select(Household).where(Household.id == household_id))).scalar_one()
    gaps = await compute_gaps(db, hh)
    return {"gaps": gaps}


async def _list_routines(tool_input: dict, household_id: str, db: AsyncSession) -> dict:
    from app.services.routines_service import RoutinesService
    routines = await RoutinesService(db).list_routines(household_id)
    return {"routines": [r.model_dump() for r in routines]}


async def _get_basket(tool_input: dict, household_id: str, db: AsyncSession) -> dict:
    from app.services import quick_basket
    items = await quick_basket.get_basket(household_id)
    return {"items": items}


async def _create_routine(tool_input: dict, household_id: str, db: AsyncSession) -> dict:
    from app.schemas.routines import RoutineCreate
    from app.services.routines_service import RoutinesService
    try:
        data = RoutineCreate(**tool_input)
    except ValidationError as e:
        return {"error": "validation_failed", "details": str(e)}
    routine = await RoutinesService(db).create(household_id, data)
    return {"routine": routine.model_dump()}


async def _edit_routine(tool_input: dict, household_id: str, db: AsyncSession) -> dict:
    from app.schemas.routines import RoutinePatch
    from app.services.routines_service import RoutinesService
    routine_id, err = _validate_routine_id(tool_input)
    if err:
        return err
    patch_fields = {k: v for k, v in tool_input.items() if k != "routine_id"}
    try:
        data = RoutinePatch(**patch_fields)
    except ValidationError as e:
        return {"error": "validation_failed", "details": str(e)}
    routine = await RoutinesService(db).patch(routine_id, household_id, data)
    if not routine:
        return {"error": "not_found", "details": f"No routine {routine_id} for this household"}
    return {"routine": routine.model_dump()}


async def _search_and_add_to_basket(tool_input: dict, household_id: str, db: AsyncSession) -> dict:
    from app.services.basket_editing_service import BasketEditingService
    from app.services import quick_basket

    query = tool_input.get("query")
    if not query:
        return {"error": "validation_failed", "details": "query is required"}
    quantity = int(tool_input.get("quantity") or 1)

    svc = BasketEditingService()
    try:
        results = await svc.search_items(db, household_id, query, limit=5)
    except Exception as e:
        return {"error": "search_failed", "details": str(e)}
    if not results:
        return {"error": "no_results", "details": f"No products found for '{query}'"}

    def _p(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    top = results[0]
    entry = await quick_basket.add_item(household_id, {
        "item_name":  _p(top, "name") or _p(top, "item_name", query),
        "brand":      _p(top, "brand"),
        "sku_id":     _p(top, "sku_id"),
        "spin_id":    _p(top, "spin_id", "") or "",
        "category":   _p(top, "category"),
        "unit":       _p(top, "unit", None) or _p(top, "quantity", None) or "units",
        "quantity":   quantity,
        "unit_price": float(_p(top, "price") or _p(top, "unit_price", 0)),
        "in_stock":   _p(top, "in_stock", True),
    })
    return {"added": entry}


async def _remove_from_basket(tool_input: dict, household_id: str, db: AsyncSession) -> dict:
    from app.services import quick_basket

    item_name = (tool_input.get("item_name") or "").strip().lower()
    if not item_name:
        return {"error": "validation_failed", "details": "item_name is required"}

    items = await quick_basket.get_basket(household_id)
    match = next((i for i in items if item_name in i["item_name"].lower()), None)
    if not match:
        return {"error": "not_found", "details": f"No basket item matching '{item_name}'"}

    removed = await quick_basket.remove_item(household_id, match["id"])
    return {"removed": removed}


async def _checkout_basket(tool_input: dict, household_id: str, db: AsyncSession) -> dict:
    from app.services import quick_checkout
    # No swiggy_address_id_override — uses the household's stored preferred
    # address, same as the REST route's default. If we're re-executing a
    # confirmed proposal, this re-fetches the basket itself rather than
    # trusting anything captured at propose-time — see Design §0's
    # confirm-time re-validation decision.
    return await quick_checkout.checkout(household_id, db)


_HANDLERS = {
    "get_weekly_nutrition":       _get_weekly_nutrition,
    "get_nutrition_gaps":         _get_nutrition_gaps,
    "list_routines":              _list_routines,
    "get_basket":                 _get_basket,
    "create_routine":             _create_routine,
    "edit_routine":                _edit_routine,
    "search_and_add_to_basket":   _search_and_add_to_basket,
    "remove_from_basket":         _remove_from_basket,
    "checkout_basket":            _checkout_basket,
}
# pause/resume/delete share one handler shape, built at import time.
_ROUTINE_ACTIONS = {"pause_routine": "pause", "resume_routine": "resume", "delete_routine": "delete"}


async def execute_tool(tool_name: str, tool_input: dict[str, Any], household_id: str, db: AsyncSession) -> dict:
    """Dispatch one tool call. Returns a JSON-serializable dict, always —
    errors come back as {"error": code, "details": message} rather than
    raising, since this feeds straight into a tool_result the model needs
    to be able to read and react to (e.g. ask a clarifying question)."""
    if tool_name in _ROUTINE_ACTIONS:
        action = _ROUTINE_ACTIONS[tool_name]
        from app.services.routines_service import RoutinesService
        routine_id, err = _validate_routine_id(tool_input)
        if err:
            return err
        svc = RoutinesService(db)
        method = getattr(svc, action)
        if action == "delete":
            deleted = await method(routine_id, household_id)
            if not deleted:
                return {"error": "not_found", "details": f"No routine {routine_id} for this household"}
            return {"deleted": True}
        routine = await method(routine_id, household_id)
        if not routine:
            return {"error": "not_found", "details": f"No routine {routine_id} for this household, or not in the right state for this action"}
        return {"routine": routine.model_dump()}

    handler = _HANDLERS.get(tool_name)
    if handler is None:
        return {"error": "unknown_tool", "details": f"No handler for tool '{tool_name}'"}
    return await handler(tool_input, household_id, db)
