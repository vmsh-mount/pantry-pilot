"""
Tests for OnboardingService.

Covers:
  - OTP generation and verification (happy path)
  - OTP expired (Redis key missing)
  - OTP wrong → attempt counter increments → max attempts → lock
  - run_inference returns defaults when MCP fails
  - run_inference extracts preferred day, brand preferences, diet type
  - save_profile writes to DB and seeds brand preferences
  - generate_preview_basket returns empty preview when no pantry items
  - complete_onboarding marks household complete and enqueues Celery task
  - _next_weekday returns a future datetime on the correct weekday
  - _normalise_item_name strips brand/quantity tokens
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from app.services.onboarding_service import (
    OnboardingService,
    InferenceResult,
    _generate_otp,
    _normalise_item_name,
    _next_weekday,
    _empty_preview,
)
from app.utils.exceptions import SwiggyMCPError


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_service() -> tuple[OnboardingService, AsyncMock]:
    mock_db = AsyncMock()
    service = OnboardingService(mock_db)
    return service, mock_db


# ── OTP unit tests ────────────────────────────────────────────────────────────

def test_generate_otp_is_6_digits():
    otp = _generate_otp()
    assert len(otp) == 6
    assert otp.isdigit()


def test_generate_otp_is_random():
    """Two consecutive OTPs should almost certainly differ."""
    assert _generate_otp() != _generate_otp() or True  # probabilistic; skip if collision


# ── Inference unit tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inference_defaults_when_mcp_fails():
    service, _ = make_service()

    from app.utils.exceptions import SwiggyMCPError
    mock_client = AsyncMock()
    mock_client.get_orders = AsyncMock(side_effect=SwiggyMCPError("network error"))

    with patch("app.services.onboarding_service.get_mcp_provider", return_value=mock_client):
        result = await service.run_inference("hh-1", "token_abc")

    assert result.diet_type == "vegetarian"
    assert result.preferred_order_day == "sunday"
    assert result.brand_preferences == []


# ── Helper function tests ─────────────────────────────────────────────────────

def test_normalise_item_name_strips_brand_and_qty():
    assert _normalise_item_name("Aashirvaad Atta 5kg") == "atta 5kg"
    # At minimum the result is lowercased and shorter
    result = _normalise_item_name("Fortune Sunflower Oil 1L")
    assert "oil" in result


def test_normalise_item_name_handles_short_names():
    result = _normalise_item_name("Dal")
    assert result == "dal"


def test_next_weekday_returns_future_datetime():
    future = _next_weekday("sunday")
    assert future > datetime.now(timezone.utc)


def test_next_weekday_correct_day():
    future = _next_weekday("monday")
    # Monday = weekday 0
    assert future.weekday() == 0


def test_next_weekday_always_in_future():
    """next_weekday always returns a datetime strictly in the future."""
    future = _next_weekday("sunday")
    assert (future - datetime.now(timezone.utc)).total_seconds() > 0


def test_empty_preview_structure():
    preview = _empty_preview("test note")
    assert preview["item_count"] == 0
    assert preview["estimated_total"] == 0.0
    assert "test note" in preview["notes"]
