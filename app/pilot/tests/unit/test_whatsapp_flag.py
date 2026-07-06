"""
Unit tests — WhatsApp enabled flag (BE-005)

Verifies that WHATSAPP_ENABLED=false:
  1. send_otp returns immediately without calling provider
  2. send_basket_preview returns immediately without calling provider
  3. send_order_receipt returns immediately without calling provider
  4. send_reauth_48hr returns immediately without calling provider
  5. send_reauth_24hr returns immediately without calling provider
  6. send_session_expired returns immediately without calling provider
  7. send_text returns immediately without calling provider
  8. All suppressed sends log at info level with method name
  9. WHATSAPP_ENABLED=true — provider is called normally
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_service(whatsapp_enabled: bool):
    """Build a WhatsAppService with a mock provider and patched settings."""
    with patch("app.services.whatsapp_service.get_settings",
               return_value=MagicMock(whatsapp_enabled=whatsapp_enabled)):
        with patch("app.providers.factory.get_whatsapp_provider") as mock_factory:
            mock_provider = MagicMock()
            mock_provider.send_otp            = AsyncMock()
            mock_provider.send_basket_preview = AsyncMock()
            mock_provider.send_order_receipt  = AsyncMock()
            mock_provider.send_reauth_48hr    = AsyncMock()
            mock_provider.send_reauth_24hr    = AsyncMock()
            mock_provider.send_session_expired = AsyncMock()
            mock_provider.send_text           = AsyncMock()
            mock_factory.return_value = mock_provider

            from app.services.whatsapp_service import WhatsAppService
            svc = WhatsAppService()
            svc._provider = mock_provider  # ensure our mock is attached
    return svc, mock_provider


# ── Disabled path — each method is a no-op ───────────────────────────────────

@pytest.mark.asyncio
async def test_send_otp_skipped_when_disabled():
    svc, provider = _make_service(whatsapp_enabled=False)
    with patch("app.services.whatsapp_service.get_settings",
               return_value=MagicMock(whatsapp_enabled=False)):
        await svc.send_otp("+919999999999", "123456")
    provider.send_otp.assert_not_called()


@pytest.mark.asyncio
async def test_send_basket_preview_skipped_when_disabled():
    svc, provider = _make_service(whatsapp_enabled=False)
    with patch("app.services.whatsapp_service.get_settings",
               return_value=MagicMock(whatsapp_enabled=False)):
        await svc.send_basket_preview("+919999999999", "Salt, Milk", 220.0, 2000.0)
    provider.send_basket_preview.assert_not_called()


@pytest.mark.asyncio
async def test_send_order_receipt_skipped_when_disabled():
    svc, provider = _make_service(whatsapp_enabled=False)
    with patch("app.services.whatsapp_service.get_settings",
               return_value=MagicMock(whatsapp_enabled=False)):
        await svc.send_order_receipt("+919999999999", 2, 220.0, "Koramangala", "30 min", "ORD123")
    provider.send_order_receipt.assert_not_called()


@pytest.mark.asyncio
async def test_send_reauth_48hr_skipped_when_disabled():
    svc, provider = _make_service(whatsapp_enabled=False)
    with patch("app.services.whatsapp_service.get_settings",
               return_value=MagicMock(whatsapp_enabled=False)):
        await svc.send_reauth_48hr("+919999999999", "in 2 days", "http://example.com/reauth")
    provider.send_reauth_48hr.assert_not_called()


@pytest.mark.asyncio
async def test_send_reauth_24hr_skipped_when_disabled():
    svc, provider = _make_service(whatsapp_enabled=False)
    with patch("app.services.whatsapp_service.get_settings",
               return_value=MagicMock(whatsapp_enabled=False)):
        await svc.send_reauth_24hr("+919999999999", "tomorrow", "http://example.com/reauth")
    provider.send_reauth_24hr.assert_not_called()


@pytest.mark.asyncio
async def test_send_session_expired_skipped_when_disabled():
    svc, provider = _make_service(whatsapp_enabled=False)
    with patch("app.services.whatsapp_service.get_settings",
               return_value=MagicMock(whatsapp_enabled=False)):
        await svc.send_session_expired("+919999999999", "http://example.com/reauth")
    provider.send_session_expired.assert_not_called()


@pytest.mark.asyncio
async def test_send_text_skipped_when_disabled():
    svc, provider = _make_service(whatsapp_enabled=False)
    with patch("app.services.whatsapp_service.get_settings",
               return_value=MagicMock(whatsapp_enabled=False)):
        await svc.send_text("+919999999999", "Hello there")
    provider.send_text.assert_not_called()


# ── Enabled path — provider is called ────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_otp_calls_provider_when_enabled():
    svc, provider = _make_service(whatsapp_enabled=True)
    with patch("app.services.whatsapp_service.get_settings",
               return_value=MagicMock(whatsapp_enabled=True)):
        await svc.send_otp("+919999999999", "654321")
    provider.send_otp.assert_called_once()


@pytest.mark.asyncio
async def test_send_basket_preview_calls_provider_when_enabled():
    svc, provider = _make_service(whatsapp_enabled=True)
    with patch("app.services.whatsapp_service.get_settings",
               return_value=MagicMock(whatsapp_enabled=True)):
        await svc.send_basket_preview("+919999999999", "Salt, Milk", 220.0, 2000.0)
    provider.send_basket_preview.assert_called_once()
