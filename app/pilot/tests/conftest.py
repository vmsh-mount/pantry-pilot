"""
pytest configuration and shared fixtures for PantryPilot pilot tests.

Fixtures provided:
  - mock_settings   — patches get_settings() with safe test values
  - mock_redis      — patches get_redis() with an AsyncMock
  - mock_db         — a bare AsyncMock SQLAlchemy session
  - anyio_backend   — pins anyio to asyncio (required by pytest-anyio)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── anyio backend ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


# ── Settings mock ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    """
    Patch get_settings() everywhere so tests never read a real .env file.
    autouse=True — applied to every test automatically.
    """
    settings = MagicMock()
    settings.app_env                = "test"
    settings.debug                  = True
    settings.database_url           = "postgresql+asyncpg://test:test@localhost/test"
    settings.redis_url              = "redis://localhost:6379/0"
    settings.swiggy_mcp_base_url    = "https://mcp.swiggy.com"
    settings.swiggy_client_id       = "test_client_id"
    settings.swiggy_redirect_uri    = "http://localhost:3000/auth/callback"
    settings.token_encryption_key   = "a" * 64   # 32-byte hex key
    settings.anthropic_api_key      = "sk-ant-test"
    settings.anthropic_model        = "claude-haiku-4-5"
    settings.interakt_api_key       = "test_interakt_key"
    settings.interakt_webhook_secret = "test_webhook_secret"
    settings.pantrypilot_whatsapp_number = "+910000000000"
    settings.jwt_secret             = "b" * 64
    settings.jwt_expiry_hours       = 24
    settings.internal_api_secret    = "test_internal_secret"
    settings.llm_provider           = "mock"
    settings.whatsapp_provider      = "mock"
    settings.mcp_provider           = "mock"
    settings.otp_provider           = "mock"
    settings.gemini_api_key         = ""
    settings.gemini_model           = "gemini-2.0-flash"
    settings.mock_mcp_base_url      = "http://localhost:8001"
    settings.swiggy_mcp_mode        = "live"
    settings.pantrypilot_dry_run     = False
    settings.whatsapp_enabled        = False
    settings.sentry_dsn             = ""
    settings.log_level              = "DEBUG"

    monkeypatch.setattr("app.config.get_settings", lambda: settings)

    # Also patch the module-level calls in services that cache settings at import.
    #
    # app.providers.factory.get_settings is on this list because get_mcp_provider()
    # branches on settings.swiggy_mcp_mode (demo vs live) — before that branch
    # existed, factory.py's own `get_settings` binding never mattered for test
    # correctness, so this gap went unnoticed. Now it does: factory.py does
    # `from app.config import get_settings` deferred, INSIDE the calling
    # function's first invocation (pantry_service.bootstrap_from_history does
    # the same, deferred-importing get_mcp_provider) — so whichever test
    # happens to trigger that chain's first-ever import in the whole pytest
    # process determines what `get_settings` stays bound to for every test
    # after it, unit or integration. Without this patch, a unit test running
    # after an integration test (which legitimately needs the real,
    # env-based settings — see tests/integration/conftest.py's own
    # mock_settings override) could silently inherit real settings instead
    # of this fixture's mock, and get routed to DemoSwiggyMCPProvider instead
    # of the SwiggyMCPClient the test actually patches.
    for module_path in [
        "app.mcp.swiggy.settings",
        "app.mcp.swiggy.get_settings",
        "app.services.auth_service.settings",
        "app.services.whatsapp_service.settings",
        "app.services.whatsapp_service.get_settings",
        "app.utils.crypto.get_settings",
        "app.agent.planning_graph.settings",
        "app.providers.factory.get_settings",
    ]:
        try:
            # Use the settings object directly for module-level `settings = get_settings()` vars;
            # use a callable lambda for `get_settings` function references.
            value = settings if module_path.split(".")[-1] == "settings" else lambda: settings
            monkeypatch.setattr(module_path, value)
        except (AttributeError, ValueError):
            pass

    return settings


# ── Redis mock ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    """AsyncMock Redis client — patches get_redis() globally."""
    redis = AsyncMock()
    redis.get    = AsyncMock(return_value=None)
    redis.set    = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.ttl    = AsyncMock(return_value=600)
    with patch("app.redis.get_redis", return_value=redis):
        yield redis


# ── DB session mock ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    """Bare AsyncMock SQLAlchemy AsyncSession."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.commit  = AsyncMock()
    db.refresh = AsyncMock()
    db.add     = MagicMock()
    db.delete  = AsyncMock()
    db.flush   = AsyncMock()
    return db


# ── Shared test data factories ────────────────────────────────────────────────

def make_scalar_result(value):
    """Return a mock that mimics db.execute().scalar_one_or_none()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def make_scalars_result(values: list):
    """Return a mock that mimics db.execute().scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result
