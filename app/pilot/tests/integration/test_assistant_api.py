"""
Integration tests — POST /v1/assistant/message, GET /v1/assistant/history
tasks/features/ai-ordering-assistant.md, Design §2.

complete_with_tools (the actual Anthropic call) is mocked throughout —
these tests exercise the propose/confirm orchestration and history
persistence, not model quality (that's the PRD's separate, non-automated
"manual/LLM-quality verification" track).
"""

import json

import pytest
from unittest.mock import AsyncMock, patch

from app.providers.llm.anthropic import AssistantToolResult
from tests.integration.conftest import create_household, set_session


def _text_result(text: str) -> AssistantToolResult:
    return AssistantToolResult(text=text, stop_reason="end_turn")


def _tool_result(name: str, tool_input: dict, tool_use_id: str = "toolu_1") -> AssistantToolResult:
    return AssistantToolResult(tool_name=name, tool_input=tool_input, tool_use_id=tool_use_id, stop_reason="tool_use")


@pytest.mark.asyncio
async def test_plain_text_turn_no_tool_call(app_client, db):
    household_id = await create_household(db)
    set_session(app_client, household_id)

    with patch(
        "app.providers.llm.anthropic.complete_with_tools",
        new=AsyncMock(return_value=_text_result("You're on track for protein this week.")),
    ):
        resp = await app_client.post("/v1/assistant/message", json={"message": "how's my protein?"})

    body = resp.json()
    assert body["success"] is True
    assert body["data"]["type"] == "text"
    assert body["data"]["message"] == "You're on track for protein this week."


@pytest.mark.asyncio
async def test_read_tool_executes_inline_no_confirmation_needed(app_client, db):
    household_id = await create_household(db)
    set_session(app_client, household_id)

    call_results = [_tool_result("list_routines", {}), _text_result("You have no routines yet.")]

    async def _fake_complete(**kwargs):
        return call_results.pop(0)

    with patch("app.providers.llm.anthropic.complete_with_tools", new=_fake_complete):
        resp = await app_client.post("/v1/assistant/message", json={"message": "what routines do I have?"})

    body = resp.json()
    assert body["success"] is True
    assert body["data"]["type"] == "text"
    assert body["data"]["message"] == "You have no routines yet."

    from app.models.db import AssistantMessage
    from sqlalchemy import select
    rows = (await db.execute(
        select(AssistantMessage).where(AssistantMessage.household_id == household_id)
    )).scalars().all()
    roles = [r.role for r in rows]
    # user turn, assistant's tool call, the tool's result, assistant's follow-up
    assert roles == ["user", "assistant", "tool_result", "assistant"]


@pytest.mark.asyncio
async def test_write_tool_is_proposed_not_executed(app_client, db):
    household_id = await create_household(db)
    set_session(app_client, household_id)

    tool_input = {
        "name": "Weekly Milk", "frequency_type": "weekly", "frequency_value": 1,
        "schedule_time": "09:00", "items": [{"item_name": "Milk", "quantity": 1}],
    }
    with patch(
        "app.providers.llm.anthropic.complete_with_tools",
        new=AsyncMock(return_value=_tool_result("create_routine", tool_input, "toolu_propose_1")),
    ):
        resp = await app_client.post("/v1/assistant/message", json={"message": "set up a weekly milk routine"})

    body = resp.json()
    assert body["success"] is True
    assert body["data"]["type"] == "proposal"
    assert body["data"]["tool_call_id"] == "toolu_propose_1"
    assert body["data"]["tool_name"] == "create_routine"
    assert "Weekly Milk" in body["data"]["preview"]

    from app.services.routines_service import RoutinesService
    routines = await RoutinesService(db).list_routines(household_id)
    assert routines == [], "Proposing must not create the routine — only confirming should."


@pytest.mark.asyncio
async def test_confirming_a_proposal_executes_it(app_client, db):
    household_id = await create_household(db)
    set_session(app_client, household_id)

    tool_input = {
        "name": "Weekly Milk", "frequency_type": "weekly", "frequency_value": 1,
        "schedule_time": "09:00", "items": [{"item_name": "Milk", "quantity": 1}],
    }
    responses = [_tool_result("create_routine", tool_input, "toolu_propose_2"), _text_result("Done — Weekly Milk is set up.")]

    async def _fake_complete(**kwargs):
        return responses.pop(0)

    with patch("app.providers.llm.anthropic.complete_with_tools", new=_fake_complete):
        propose_resp = await app_client.post("/v1/assistant/message", json={"message": "set up a weekly milk routine"})
        assert propose_resp.json()["data"]["type"] == "proposal"

        confirm_resp = await app_client.post("/v1/assistant/message", json={"confirm_tool_call_id": "toolu_propose_2"})

    body = confirm_resp.json()
    assert body["success"] is True
    assert body["data"]["type"] == "text"
    assert "Done" in body["data"]["message"]

    from app.services.routines_service import RoutinesService
    routines = await RoutinesService(db).list_routines(household_id)
    assert len(routines) == 1
    assert routines[0].name == "Weekly Milk"


@pytest.mark.asyncio
async def test_confirming_same_proposal_twice_is_rejected(app_client, db):
    household_id = await create_household(db)
    set_session(app_client, household_id)

    tool_input = {
        "name": "Weekly Milk", "frequency_type": "weekly", "frequency_value": 1,
        "schedule_time": "09:00", "items": [{"item_name": "Milk", "quantity": 1}],
    }
    responses = [_tool_result("create_routine", tool_input, "toolu_propose_3"), _text_result("Done.")]

    async def _fake_complete(**kwargs):
        return responses.pop(0) if responses else _text_result("(unexpected extra call)")

    with patch("app.providers.llm.anthropic.complete_with_tools", new=_fake_complete):
        await app_client.post("/v1/assistant/message", json={"message": "set up a weekly milk routine"})
        await app_client.post("/v1/assistant/message", json={"confirm_tool_call_id": "toolu_propose_3"})
        second_confirm = await app_client.post("/v1/assistant/message", json={"confirm_tool_call_id": "toolu_propose_3"})

    body = second_confirm.json()
    assert body["success"] is False
    assert body["error"]["code"] == "ALREADY_EXECUTED"

    from app.services.routines_service import RoutinesService
    routines = await RoutinesService(db).list_routines(household_id)
    assert len(routines) == 1, "Must not have created the routine twice."


@pytest.mark.asyncio
async def test_confirming_unknown_tool_call_id_returns_not_found(app_client, db):
    household_id = await create_household(db)
    set_session(app_client, household_id)

    resp = await app_client.post("/v1/assistant/message", json={"confirm_tool_call_id": "toolu_never_proposed"})
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected(app_client):
    resp = await app_client.post("/v1/assistant/message", json={"message": "hi"})
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_get_history_returns_full_turn_list(app_client, db):
    household_id = await create_household(db)
    set_session(app_client, household_id)

    with patch(
        "app.providers.llm.anthropic.complete_with_tools",
        new=AsyncMock(return_value=_text_result("Hello!")),
    ):
        await app_client.post("/v1/assistant/message", json={"message": "hi"})

    resp = await app_client.get("/v1/assistant/history")
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]) == 2
    assert body["data"][0]["role"] == "user"
    assert body["data"][0]["content"] == "hi"
    assert body["data"][1]["role"] == "assistant"
    assert body["data"][1]["content"] == "Hello!"
