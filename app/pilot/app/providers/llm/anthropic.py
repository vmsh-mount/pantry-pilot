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
