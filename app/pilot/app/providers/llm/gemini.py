import asyncio
from app.config import get_settings


class GeminiLLMProvider:
    def __init__(self):
        s = get_settings()
        try:
            import google.generativeai as genai
            genai.configure(api_key=s.gemini_api_key)
            self._model = genai.GenerativeModel(s.gemini_model or "gemini-2.0-flash")
            self._genai = genai
        except ImportError:
            raise RuntimeError(
                "google-generativeai package not installed. Run: pip install google-generativeai"
            )

    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        combined = f"{system}\n\n{user}"
        response = await asyncio.to_thread(
            self._model.generate_content,
            combined,
            generation_config=self._genai.types.GenerationConfig(max_output_tokens=max_tokens),
        )
        return response.text
