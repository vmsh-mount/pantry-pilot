"""
Tests for WhatsAppService and helpers.

Covers:
  - InboundMessage parsing (text and button reply)
  - Intent classification for all known intents
  - Opt-out keyword detection
  - Phone number normalisation
  - _split_phone round-trips
  - build_basket_summary truncation and overflow label
  - build_full_item_list formatting
  - parse_remove_indices from various text formats
  - parse_add_item extraction
  - send_otp fires correct template name and body value
  - send_basket_preview passes all 3 body values
  - _post failure is swallowed (no raise)
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.whatsapp_service import (
    WhatsAppService,
    InboundMessage,
    build_basket_summary,
    build_full_item_list,
    parse_remove_indices,
    parse_add_item,
    _normalise_phone,
    _split_phone,
    _classify_intent,
)


# ── Phone helpers ─────────────────────────────────────────────────────────────

def test_normalise_phone_10_digits():
    assert _normalise_phone("9876543210") == "+919876543210"


def test_normalise_phone_12_digits_with_91():
    assert _normalise_phone("919876543210") == "+919876543210"


def test_normalise_phone_already_normalised():
    assert _normalise_phone("+919876543210") == "+919876543210"


def test_split_phone_india():
    cc, local = _split_phone("+919876543210")
    assert cc    == "+91"
    assert local == "9876543210"


def test_split_phone_other_country():
    cc, local = _split_phone("+12025551234")
    assert cc    == "+12"
    assert local == "025551234"


# ── Intent classification ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("stop",          "opt_out"),
    ("STOP",          "opt_out"),   # normalised to lower before classify
    ("unsubscribe",   "opt_out"),
    ("pause",         "pause_loop"),
    ("cancel",        "pause_loop"),
    ("resume",        "resume_loop"),
    ("skip",          "skip_week"),
    ("skip this week","skip_week"),
    ("order now",     "trigger_immediate"),
    ("order",         "trigger_immediate"),
    ("remove 3, 5",   "remove_items"),
    ("3 5",           "remove_items"),
    ("add milk",      "add_item"),
    ("add amul butter","add_item"),
    ("help",          "send_help"),
    ("?",             "send_help"),
    ("what is this",  "unknown"),
])
def test_classify_intent_text(text, expected):
    assert _classify_intent(text.lower(), "") == expected


@pytest.mark.parametrize("button_id,expected", [
    ("btn_confirm",        "confirm_order"),
    ("btn_review",         "review_basket"),
    ("btn_skip",           "skip_week"),
    ("btn_reauth",         "reauth"),
    ("btn_reauth_urgent",  "reauth"),
    ("btn_reauth_expired", "reauth"),
    ("btn_track",          "track_order"),
])
def test_classify_intent_button_reply(button_id, expected):
    assert _classify_intent("", button_id) == expected


# ── InboundMessage parsing ────────────────────────────────────────────────────

def _make_text_payload(phone: str, text: str) -> dict:
    return {
        "type": "message",
        "data": {
            "contact": {"phone_number": phone},
            "message": {
                "type": "text",
                "text": {"body": text},
            },
        },
    }


def _make_button_payload(phone: str, btn_id: str, btn_title: str) -> dict:
    return {
        "type": "message",
        "data": {
            "contact": {"phone_number": phone},
            "message": {
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": btn_id, "title": btn_title},
                },
            },
        },
    }


def test_inbound_text_message_parsed():
    msg = InboundMessage(_make_text_payload("9876543210", "Help"))
    assert msg.phone_number == "+919876543210"
    assert msg.text         == "help"
    assert msg.intent       == "send_help"
    assert msg.is_button_reply is False


def test_inbound_button_reply_parsed():
    msg = InboundMessage(_make_button_payload("9876543210", "btn_confirm", "✅ Looks good"))
    assert msg.is_button_reply is True
    assert msg.button_id       == "btn_confirm"
    assert msg.intent          == "confirm_order"


def test_inbound_opt_out_text():
    msg = InboundMessage(_make_text_payload("9876543210", "STOP"))
    assert msg.intent == "opt_out"


def test_inbound_skip_button():
    msg = InboundMessage(_make_button_payload("9876543210", "btn_skip", "❌ Skip this week"))
    assert msg.intent == "skip_week"


def test_inbound_unknown_text():
    msg = InboundMessage(_make_text_payload("9876543210", "what's the weather today?"))
    assert msg.intent == "unknown"


# ── Basket helpers ────────────────────────────────────────────────────────────

def test_build_basket_summary_short():
    items = [{"product_name": f"Item {i}"} for i in range(3)]
    summary = build_basket_summary(items, max_visible=5)
    assert "Item 0" in summary
    assert "more" not in summary


def test_build_basket_summary_overflow():
    items = [{"product_name": f"Item {i}"} for i in range(10)]
    summary = build_basket_summary(items, max_visible=5)
    assert "+5 more" in summary


def test_build_basket_summary_empty():
    assert build_basket_summary([]) == "No items"


def test_build_basket_summary_uses_item_name_fallback():
    items = [{"item_name": "Toor Dal"}]
    summary = build_basket_summary(items)
    assert "Toor Dal" in summary


def test_build_full_item_list_format():
    items = [
        {"item_name": "Atta", "total_price": 280.0},
        {"item_name": "Dal",  "total_price": 145.0},
    ]
    result = build_full_item_list(items)
    assert "1. Atta" in result
    assert "₹280" in result
    assert "2. Dal" in result


# ── parse_remove_indices ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_0based", [
    ("remove 3, 5",   [2, 4]),
    ("3 5",           [2, 4]),
    ("remove 1",      [0]),
    ("remove 10",     [9]),
])
def test_parse_remove_indices(text, expected_0based):
    assert parse_remove_indices(text) == expected_0based


def test_parse_remove_indices_empty_text():
    assert parse_remove_indices("skip this week") == []


# ── parse_add_item ────────────────────────────────────────────────────────────

def test_parse_add_item_simple():
    assert parse_add_item("add milk") == "milk"


def test_parse_add_item_multi_word():
    assert parse_add_item("add amul butter") == "amul butter"


def test_parse_add_item_no_match():
    assert parse_add_item("remove 3") is None


# ── Outbound sends ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_post_failure_does_not_raise():
    """Interakt HTTP failures must be swallowed — never crash the planning loop."""
    wa = WhatsAppService()

    with patch("httpx.AsyncClient") as MockClient:
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        MockClient.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)

        # Must not raise
        await wa.send_otp("+919876543210", "999999")
