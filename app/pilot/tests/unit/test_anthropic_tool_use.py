"""
Unit tests — complete_with_tools (app/providers/llm/anthropic.py)

No existing coverage of the Anthropic provider at all before this file.
Mocks anthropic.AsyncAnthropic directly (complete_with_tools constructs its
own client internally, same as AnthropicLLMProvider.complete() already
does) — never makes a real API call.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.providers.llm.anthropic import complete_with_tools


def _text_response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
    )


def _tool_use_response(name: str, tool_input: dict, tool_use_id: str = "toolu_123", stop_reason: str = "tool_use"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name=name, input=tool_input, id=tool_use_id)],
        stop_reason=stop_reason,
    )


@pytest.mark.asyncio
async def test_plain_text_response_returns_text_not_tool_call():
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=_text_response("You're on track for protein this week."))

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        result = await complete_with_tools(
            system="You are a helpful assistant.",
            messages=[{"role": "user", "content": "how's my protein this week?"}],
            tools=[],
        )

    assert result.is_tool_call is False
    assert result.text == "You're on track for protein this week."
    assert result.tool_name is None


@pytest.mark.asyncio
async def test_tool_use_response_surfaces_name_input_and_id():
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=_tool_use_response(
        "create_routine",
        {"name": "Weekly Milk", "frequency_type": "weekly", "frequency_value": 1,
         "schedule_time": "09:00", "items": [{"item_name": "Amul Toned Milk", "quantity": 1}]},
    ))

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        result = await complete_with_tools(
            system="You are a helpful assistant.",
            messages=[{"role": "user", "content": "set up a weekly milk routine"}],
            tools=[{"name": "create_routine", "description": "...", "input_schema": {}}],
        )

    assert result.is_tool_call is True
    assert result.tool_name == "create_routine"
    assert result.tool_input["name"] == "Weekly Milk"
    assert result.tool_use_id == "toolu_123"
    assert result.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_only_first_tool_use_block_surfaced_when_multiple_present():
    mock_client = AsyncMock()
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="tool_use", name="get_weekly_nutrition", input={}, id="toolu_1"),
            SimpleNamespace(type="tool_use", name="list_routines", input={}, id="toolu_2"),
        ],
        stop_reason="tool_use",
    )
    mock_client.messages.create = AsyncMock(return_value=response)

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        result = await complete_with_tools(system="x", messages=[], tools=[])

    assert result.tool_name == "get_weekly_nutrition"
    assert result.tool_use_id == "toolu_1"


@pytest.mark.asyncio
async def test_messages_and_tools_passed_through_unmodified():
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=_text_response("ok"))
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    tools = [{"name": "get_basket", "description": "...", "input_schema": {}}]

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        await complete_with_tools(system="sys", messages=messages, tools=tools, max_tokens=512)

    _, kwargs = mock_client.messages.create.call_args
    assert kwargs["messages"] == messages
    assert kwargs["tools"] == tools
    assert kwargs["system"] == "sys"
    assert kwargs["max_tokens"] == 512
