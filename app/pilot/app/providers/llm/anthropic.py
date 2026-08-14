import anthropic
from app.config import get_settings


class AnthropicLLMProvider:
    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        s = get_settings()
        client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
        msg = await client.messages.create(
            model=s.anthropic_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text


# ── AI ordering assistant: native tool use ──────────────────────────────────
#
# Deliberately NOT part of the LLMProvider protocol above (app/providers/base.py)
# or the get_llm_provider() factory — that protocol is kept minimal and
# provider-agnostic on purpose (Anthropic/Gemini/Groq all implement plain
# complete()), and tool-use request/response shapes differ too much across
# providers to force through one shared interface. This is a scoped
# exception for one feature, not a precedent — see
# tasks/features/ai-ordering-assistant.md, Architecture Decision.
# The AI assistant's tool-dispatch layer calls complete_with_tools()
# directly; nothing else in the app should import this.

class AssistantToolResult:
    """One turn's result from complete_with_tools() — either plain text, or
    a proposed tool call (name + input + the id Anthropic needs to
    correlate the eventual tool_result back to this specific call)."""

    def __init__(
        self,
        *,
        text: str | None = None,
        tool_name: str | None = None,
        tool_input: dict | None = None,
        tool_use_id: str | None = None,
        stop_reason: str | None = None,
    ):
        self.text = text
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.tool_use_id = tool_use_id
        self.stop_reason = stop_reason

    @property
    def is_tool_call(self) -> bool:
        return self.tool_name is not None


async def complete_with_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 1024,
) -> AssistantToolResult:
    """
    Direct Anthropic SDK call with native tool use.

    `messages` is the running conversation in Anthropic's own message
    format — [{"role": "user"|"assistant", "content": ...}, ...]. Anthropic
    has no separate "tool" role: a tool's result is fed back as a "user"
    turn containing a `{"type": "tool_result", "tool_use_id": ..., "content": ...}`
    content block — construct that at the call site (the assistant's
    message-endpoint handler), this function just passes `messages` through
    unmodified.

    `tools` is Anthropic's tool-schema format:
    [{"name": str, "description": str, "input_schema": {JSON Schema}}, ...].

    Only the FIRST tool_use block in the response is surfaced — the tool
    set here is small and none of v1's tools are designed to be called in
    parallel within one turn (see Design §3's tool table); if that changes,
    this is the point to revisit, not silently drop additional tool calls.
    """
    s = get_settings()
    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
    response = await client.messages.create(
        model=s.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        tools=tools,
    )

    tool_use_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use_block is not None:
        return AssistantToolResult(
            tool_name=tool_use_block.name,
            tool_input=tool_use_block.input,
            tool_use_id=tool_use_block.id,
            stop_reason=response.stop_reason,
        )

    text_block = next((b for b in response.content if b.type == "text"), None)
    return AssistantToolResult(
        text=text_block.text if text_block is not None else "",
        stop_reason=response.stop_reason,
    )
