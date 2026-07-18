from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_name: str = "PantryPilot"
    app_env: str = "development"  # development | staging | production
    debug: bool = True

    # Database
    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # Swiggy MCP
    swiggy_mcp_base_url: str = "https://mcp.swiggy.com"
    swiggy_client_id: str = ""   # auto-registered via DCR if blank
    swiggy_redirect_uri: str = "http://localhost:8000/v1/auth/callback"

    # Token encryption (AES-256 — 32-byte hex key)
    token_encryption_key: str

    # Anthropic
    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_haiku_model: str = "claude-haiku-4-5-20251001"

    # USDA FoodData Central (nutrition resolution)
    usda_api_key: str = ""

    # Interakt (WhatsApp BSP — production)
    interakt_api_key: str = ""
    interakt_webhook_secret: str = ""
    pantrypilot_whatsapp_number: str = ""

    # Twilio (WhatsApp — sandbox / alternative)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "+14155238886"  # default Twilio sandbox number
    twilio_otp_template_sid: str = ""            # Content SID for OTP template (HX...)
    twilio_basket_preview_template_sid: str = "" # Content SID for basket preview template (HX...)

    # JWT (web session)
    jwt_secret: str
    jwt_expiry_hours: int = 24

    # Internal service key
    internal_api_secret: str = ""

    # Provider selection (leave blank to auto-select: local env → mock, else → real)
    llm_provider: str = ""       # anthropic | gemini | mock
    whatsapp_provider: str = ""  # interakt | mock
    mcp_provider: str = ""       # swiggy | mock
    otp_provider: str = ""       # redis | mock

    # Google Gemini (alternative LLM)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # Groq (fast free-tier LLM — llama, mixtral, gemma)
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Cockpit (frontend) base URL — used for post-auth redirects
    cockpit_url: str = "http://localhost:3000"

    # Mock MCP server
    # base_url   — Docker-internal, used for server→server calls (token exchange, logout)
    # public_url — browser-accessible, used for the OAuth authorize redirect
    mock_mcp_base_url: str = "http://localhost:8001"
    mock_mcp_public_url: str = "http://localhost:8001"

    # Dry run — mocks Swiggy checkout so no real order is placed
    pantrypilot_dry_run: bool = False

    # WhatsApp — set to true in production; false silences all outbound messages
    whatsapp_enabled: bool = False

    # Monitoring
    sentry_dsn: str = ""
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
