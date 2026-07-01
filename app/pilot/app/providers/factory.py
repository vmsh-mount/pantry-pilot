from app.config import get_settings
from app.providers.base import LLMProvider, WhatsAppProvider, MCPProvider, OTPProvider


def get_llm_provider() -> LLMProvider:
    s = get_settings()
    provider = s.llm_provider or "anthropic"
    if provider == "gemini":
        from app.providers.llm.gemini import GeminiLLMProvider
        return GeminiLLMProvider()
    if provider == "groq":
        from app.providers.llm.groq import GroqLLMProvider
        return GroqLLMProvider()
    from app.providers.llm.anthropic import AnthropicLLMProvider
    return AnthropicLLMProvider()


def get_whatsapp_provider() -> WhatsAppProvider:
    s = get_settings()
    provider = s.whatsapp_provider or "interakt"
    if provider == "twilio":
        from app.providers.whatsapp.twilio import TwilioWhatsAppProvider
        return TwilioWhatsAppProvider()
    from app.providers.whatsapp.interakt import InteraktWhatsAppProvider
    return InteraktWhatsAppProvider()


def get_mcp_provider(access_token: str) -> MCPProvider:
    from app.providers.mcp.swiggy import SwiggyMCPProvider
    return SwiggyMCPProvider(access_token)


def get_otp_provider() -> OTPProvider:
    from app.providers.otp.redis_otp import RedisOTPProvider
    return RedisOTPProvider()
