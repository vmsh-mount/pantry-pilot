"""
WhatsAppService — all outbound WhatsApp communication via Interakt BSP.

Responsibilities:
  - Send each of the 6 pre-approved Meta templates
  - Parse and route inbound webhook payloads
  - Handle opt-out (STOP) flag propagation to DB
  - Abstract the Interakt API so the rest of the app never touches it directly

Template inventory:
  1. pantrypilot_otp_verification   — OTP during onboarding
  2. pantrypilot_basket_preview     — weekly basket card with 3 quick-reply buttons
  3. pantrypilot_order_receipt      — order confirmation after placement
  4. pantrypilot_reauth_48hr        — token expiring in 2 days
  5. pantrypilot_reauth_24hr        — token expiring tomorrow (urgent)
  6. pantrypilot_session_expired    — session already expired, order failed

Inbound routing (intent → handler):
  STOP / unsubscribe       → opt_out
  pause / cancel / stop    → pause_loop
  resume                   → resume_loop
  skip / skip this week    → skip_week
  order now                → trigger_immediate
  remove <nums>            → remove_items
  add <item>               → add_item
  help                     → send_help
  button_reply             → handle_button_reply
  <anything else>          → send_fallback

Interakt API endpoint: POST https://api.interakt.ai/v1/public/message/
Auth: Basic <base64(api_key)>
"""

import re
from typing import Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)

# Opt-out keywords Meta mandates we honour
_OPT_OUT_KEYWORDS = {"stop", "unsubscribe", "optout", "opt out", "opt-out"}

# Country code assumed for Indian numbers stored without it
_DEFAULT_COUNTRY_CODE = "+91"


# ── Parsed inbound message ────────────────────────────────────────────────────

class InboundMessage:
    """
    Normalised inbound WhatsApp message from Interakt webhook payload.
    Handles both free-text replies and button/quick-reply interactions.
    """

    def __init__(self, payload: dict):
        self.raw              = payload
        self.phone_number: str     = ""
        self.text: str             = ""
        self.is_button_reply: bool = False
        self.button_id: str        = ""
        self.intent: str           = "unknown"

        self._parse(payload)
        self.intent = _classify_intent(self.text, self.button_id)

    def _parse(self, payload: dict) -> None:
        """
        Interakt sends payloads in this shape:
        {
          "type": "message",
          "data": {
            "message": {
              "type": "text" | "interactive",
              "text": {"body": "..."},
              "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "...", "title": "..."}
              }
            },
            "contact": {"phone_number": "919876543210"}
          }
        }
        """
        data    = payload.get("data", {})
        contact = data.get("contact", {})
        message = data.get("message", {})

        raw_phone = contact.get("phone_number", "")
        self.phone_number = _normalise_phone(raw_phone)

        msg_type = message.get("type", "text")

        if msg_type == "interactive":
            interactive = message.get("interactive", {})
            if interactive.get("type") == "button_reply":
                btn = interactive["button_reply"]
                self.button_id      = btn.get("id", "")
                self.text           = btn.get("title", "").lower().strip()
                self.is_button_reply = True
        else:
            self.text = (
                message.get("text", {}).get("body", "")
                .lower()
                .strip()
            )


# ── Main service class ────────────────────────────────────────────────────────

class WhatsAppService:
    """
    Stateless service — one instance per use, no DB dependency.
    Delegates all outbound sending to the configured WhatsAppProvider.
    Business logic helpers (intent classification, summary formatting) live here.
    """

    def __init__(self):
        from app.providers.factory import get_whatsapp_provider
        self._provider = get_whatsapp_provider()

    # ── Outbound: Template 1 — OTP ────────────────────────────────────────────

    async def send_otp(self, phone_number: str, otp: str) -> None:
        """Send OTP during onboarding WhatsApp verification."""
        await self._provider.send_otp(_normalise_phone(phone_number), otp)
        logger.info("whatsapp_otp_sent", phone_last4=phone_number[-4:])

    # ── Outbound: Template 2 — Basket preview ─────────────────────────────────

    async def send_basket_preview(
        self,
        phone_number:   str,
        basket_summary: str,
        basket_total:   float,
        weekly_budget:  float,
    ) -> None:
        """Send weekly basket card with 3 quick-reply buttons."""
        await self._provider.send_basket_preview(
            _normalise_phone(phone_number),
            basket_summary,
            basket_total,
            weekly_budget,
        )
        logger.info(
            "whatsapp_basket_preview_sent",
            phone_last4  = phone_number[-4:],
            basket_total = basket_total,
        )

    # ── Outbound: Template 3 — Order receipt ──────────────────────────────────

    async def send_order_receipt(
        self,
        phone_number:        str,
        item_count:          int,
        grand_total:         float,
        delivery_area:       str,
        estimated_delivery:  str,
        swiggy_order_id:     str,
    ) -> None:
        """Confirm a placed Swiggy Instamart order."""
        await self._provider.send_order_receipt(
            _normalise_phone(phone_number),
            item_count,
            grand_total,
            delivery_area,
            estimated_delivery,
            swiggy_order_id,
        )
        logger.info(
            "whatsapp_order_receipt_sent",
            phone_last4 = phone_number[-4:],
            grand_total = grand_total,
        )

    # ── Outbound: Template 4 — Re-auth 48hr nudge ─────────────────────────────

    async def send_reauth_48hr(
        self,
        phone_number:  str,
        expiry_label:  str,
        reauth_url:    str,
    ) -> None:
        await self._provider.send_reauth_48hr(
            _normalise_phone(phone_number), expiry_label, reauth_url
        )
        logger.info("whatsapp_reauth_48hr_sent", phone_last4=phone_number[-4:])

    # ── Outbound: Template 5 — Re-auth 24hr (urgent) ─────────────────────────

    async def send_reauth_24hr(
        self,
        phone_number: str,
        expiry_label: str,
        reauth_url:   str,
    ) -> None:
        await self._provider.send_reauth_24hr(
            _normalise_phone(phone_number), expiry_label, reauth_url
        )
        logger.info("whatsapp_reauth_24hr_sent", phone_last4=phone_number[-4:])

    # ── Outbound: Template 6 — Session expired ────────────────────────────────

    async def send_session_expired(self, phone_number: str, reauth_url: str) -> None:
        await self._provider.send_session_expired(
            _normalise_phone(phone_number), reauth_url
        )
        logger.info("whatsapp_session_expired_sent", phone_last4=phone_number[-4:])

    # ── Outbound: Free-form text ──────────────────────────────────────────────

    async def send_text(
        self,
        phone_number: str,
        text:         str,
        buttons:      Optional[list[dict]] = None,
    ) -> None:
        """
        Send a free-form text message.
        Only valid within a 24-hour conversation window (i.e. user messaged first).
        """
        await self._provider.send_text(_normalise_phone(phone_number), text, buttons)

    # ── Inbound routing ───────────────────────────────────────────────────────

    @staticmethod
    def parse_inbound(payload: dict) -> "InboundMessage":
        """Parse raw Interakt webhook payload into a structured InboundMessage."""
        return InboundMessage(payload)


# ── Conversation reply helpers ────────────────────────────────────────────────

def build_basket_summary(items: list[dict], max_visible: int = 5) -> str:
    """
    Format basket items into a WhatsApp-friendly summary string.
    e.g. "Aashirvaad Atta 5kg, Toor Dal 1kg, Amul Butter 500g (+7 more)"
    """
    if not items:
        return "No items"

    visible = items[:max_visible]
    names   = [i.get("product_name") or i.get("item_name", "Unknown") for i in visible]
    summary = ", ".join(names)

    overflow = len(items) - max_visible
    if overflow > 0:
        summary += f" (+{overflow} more)"

    return summary


def build_full_item_list(items: list[dict]) -> str:
    """
    Format basket items as a numbered list for the review flow.
    e.g.
      1. Aashirvaad Atta 5kg — ₹280
      2. Toor Dal 1kg — ₹145
      ...
    """
    lines = []
    for i, item in enumerate(items, 1):
        name  = item.get("product_name") or item.get("item_name", "Unknown")
        price = item.get("total_price", 0)
        lines.append(f"{i}. {name} — ₹{int(price)}")
    return "\n".join(lines)


def parse_remove_indices(text: str) -> list[int]:
    """
    Extract 1-based item indices from user text like "remove 3, 5, 7" or "3 7".
    Returns 0-based indices for list access.
    """
    nums = re.findall(r"\d+", text)
    return [int(n) - 1 for n in nums if int(n) >= 1]


def parse_add_item(text: str) -> Optional[str]:
    """
    Extract item name from user text like "add milk" or "add amul butter".
    Returns None if can't parse.
    """
    match = re.search(r"add\s+(.+)", text.lower())
    return match.group(1).strip() if match else None


# ── Private helpers ───────────────────────────────────────────────────────────

def _normalise_phone(raw: str) -> str:
    """Ensure phone number starts with + and country code."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    if len(digits) == 13 and digits.startswith("091"):
        return f"+{digits[1:]}"
    return f"+{digits}" if not raw.startswith("+") else raw


def _split_phone(phone: str) -> tuple[str, str]:
    """
    Split a normalised phone like "+919876543210" into ("+91", "9876543210").
    Defaults to India if no country code prefix found.
    """
    if phone.startswith("+91") and len(phone) > 3:
        return "+91", phone[3:]
    if phone.startswith("+") and len(phone) > 1:
        # Try to split at a known 2-3 digit country code boundary
        return phone[:3], phone[3:]
    return _DEFAULT_COUNTRY_CODE, phone


def _classify_intent(text: str, button_id: str) -> str:
    """
    Map user text / button ID to a canonical intent string.
    Intents are consumed by the Celery task that processes inbound messages.
    """
    # Button replies take precedence
    if button_id:
        intent_map = {
            "btn_confirm":       "confirm_order",
            "btn_review":        "review_basket",
            "btn_skip":          "skip_week",
            "btn_track":         "track_order",
            "btn_reauth":        "reauth",
            "btn_reauth_urgent": "reauth",
            "btn_reauth_expired":"reauth",
        }
        if button_id in intent_map:
            return intent_map[button_id]

    text = text.lower().strip()

    # Opt-out
    if text in _OPT_OUT_KEYWORDS:
        return "opt_out"

    # Pause / cancel
    if any(kw in text for kw in ("pause", "cancel", "stop")):
        return "pause_loop"

    # Resume
    if "resume" in text:
        return "resume_loop"

    # Skip
    if "skip" in text:
        return "skip_week"

    # Order now
    if "order now" in text or text == "order":
        return "trigger_immediate"

    # Remove items
    if "remove" in text or re.match(r"^[\d\s,]+$", text):
        return "remove_items"

    # Add item
    if text.startswith("add "):
        return "add_item"

    # Help
    if text in ("help", "?", "commands"):
        return "send_help"

    return "unknown"
