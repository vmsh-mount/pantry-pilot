"""
Groq LLM provider — OpenAI-compatible API, very fast inference (LPU hardware).
Free tier available at console.groq.com. Supports llama, mixtral, gemma models.
"""

import httpx
from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_GROQ_API = "https://api.groq.com/openai/v1/chat/completions"


class GroqLLMProvider:
    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        s = get_settings()

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _GROQ_API,
                json={
                    "model":      s.groq_model,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                },
                headers={
                    "Authorization": f"Bearer {s.groq_api_key}",
                    "Content-Type":  "application/json",
                },
            )

        if not resp.is_success:
            raise RuntimeError(f"Groq request failed: {resp.status_code} {resp.text[:300]}")

        data = resp.json()
        logger.info(
            "groq_llm_complete",
            model=s.groq_model,
            tokens=data.get("usage", {}).get("total_tokens"),
        )
        return data["choices"][0]["message"]["content"]
