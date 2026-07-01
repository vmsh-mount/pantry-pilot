"""
TwilioWhatsAppProvider — sends WhatsApp messages via Twilio's Messaging API.

Works with both:
  - Twilio WhatsApp Sandbox (dev/testing — no business account needed)
  - Twilio WhatsApp approved senders (production)

Sandbox setup:
  1. Sign up at twilio.com (free trial)
  2. Go to Messaging → Try it out → Send a WhatsApp message
  3. WhatsApp your sandbox number "join <word>" to activate
  4. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM in .env

No Meta template approval needed in sandbox mode — sends plain text freely.
"""

import base64
import httpx
from app.config import get_settings
from app.utils.logging import get_logger

logger   = get_logger(__name__)
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Twilio Messaging API endpoint
_TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"


class TwilioWhatsAppProvider:
    """Sends WhatsApp messages via Twilio REST API."""

    # ── OTP ───────────────────────────────────────────────────────────────────

    async def send_otp(self, phone: str, otp: str) -> None:
        """Send OTP via Twilio Content Template (required for WhatsApp delivery)."""
        s = get_settings()
        if s.twilio_otp_template_sid:
            await self._send_template(phone, s.twilio_otp_template_sid, {"1": otp, "2": "10"})
        else:
            # Fallback: plain text (only works if Twilio ever re-enables it)
            body = (
                f"🥦 *PantryPilot* — Your verification code is:\n\n"
                f"*{otp}*\n\n"
                f"Valid for 10 minutes. Do not share this code."
            )
            await self._send(phone, body)
        logger.info("twilio_otp_sent", phone_last4=phone[-4:])

    # ── Basket preview ────────────────────────────────────────────────────────

    async def send_basket_preview(
        self,
        phone:   str,
        summary: str,
        total:   float,
        budget:  float,
    ) -> None:
        s = get_settings()
        if s.twilio_basket_preview_template_sid:
            await self._send_template(
                phone,
                s.twilio_basket_preview_template_sid,
                {"1": summary, "2": str(int(total)), "3": str(int(budget))},
            )
        else:
            body = (
                f"🛒 *Your weekly grocery basket is ready!*\n\n"
                f"{summary}\n\n"
                f"*Estimated total:* ₹{int(total)}\n"
                f"*Your budget:* ₹{int(budget)}\n\n"
                f"Reply *YES* to confirm and place the order, "
                f"*SKIP* to skip this week, or visit the app to review items."
            )
            await self._send(phone, body)
        logger.info("twilio_basket_preview_sent", phone_last4=phone[-4:], total=total)

    # ── Order receipt ─────────────────────────────────────────────────────────

    async def send_order_receipt(
        self,
        phone:      str,
        item_count: int,
        total:      float,
        area:       str,
        eta:        str,
        order_id:   str,
    ) -> None:
        body = (
            f"✅ *Order placed on Swiggy Instamart!*\n\n"
            f"{item_count} items • ₹{int(total)}\n"
            f"📍 Delivering to {area}\n"
            f"⏱ ETA: {eta}\n\n"
            f"Track: https://www.swiggy.com/order/{order_id}"
        )
        await self._send(phone, body)
        logger.info("twilio_order_receipt_sent", phone_last4=phone[-4:], order_id=order_id)

    # ── Re-auth nudges ────────────────────────────────────────────────────────

    async def send_reauth_48hr(self, phone: str, expiry_label: str, reauth_url: str) -> None:
        body = (
            f"⚠️ *PantryPilot — Action needed*\n\n"
            f"Your Swiggy connection expires {expiry_label}.\n"
            f"Reconnect to keep your weekly baskets running:\n{reauth_url}"
        )
        await self._send(phone, body)

    async def send_reauth_24hr(self, phone: str, expiry_label: str, reauth_url: str) -> None:
        body = (
            f"🚨 *PantryPilot — Urgent*\n\n"
            f"Your Swiggy session expires {expiry_label}.\n"
            f"Reconnect now or this week's basket will be skipped:\n{reauth_url}"
        )
        await self._send(phone, body)

    async def send_session_expired(self, phone: str, reauth_url: str) -> None:
        body = (
            f"❌ *PantryPilot — Session expired*\n\n"
            f"Your Swiggy connection has expired. "
            f"Reconnect to resume weekly grocery planning:\n{reauth_url}"
        )
        await self._send(phone, body)

    # ── Raw text ──────────────────────────────────────────────────────────────

    async def send_text(self, phone: str, text: str, buttons=None) -> None:
        """Send a plain text message. Buttons are not supported on Twilio sandbox."""
        await self._send(phone, text)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _send_template(self, to_phone: str, content_sid: str, variables: dict) -> None:
        """Send a WhatsApp message using a Twilio Content Template."""
        import json
        s       = get_settings()
        to_wa   = f"whatsapp:{_e164(to_phone)}"
        from_wa = f"whatsapp:{s.twilio_whatsapp_from}"
        auth    = base64.b64encode(
            f"{s.twilio_account_sid}:{s.twilio_auth_token}".encode()
        ).decode()
        url = _TWILIO_API.format(account_sid=s.twilio_account_sid)

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                data={
                    "From":              from_wa,
                    "To":                to_wa,
                    "ContentSid":        content_sid,
                    "ContentVariables":  json.dumps(variables),
                },
                headers={"Authorization": f"Basic {auth}"},
            )

        if not resp.is_success:
            err  = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            code = err.get("code", resp.status_code)
            msg  = err.get("message", resp.text[:200])
            logger.error("twilio_template_send_failed", status=resp.status_code, code=code, message=msg, to=to_phone[-4:])
        else:
            logger.info("twilio_message_queued", sid=resp.json().get("sid", "")[:8])

    async def _send(self, to_phone: str, body: str) -> None:
        s = get_settings()

        # Ensure phone is in E.164 format
        to_wa   = f"whatsapp:{_e164(to_phone)}"
        from_wa = f"whatsapp:{s.twilio_whatsapp_from}"

        auth    = base64.b64encode(
            f"{s.twilio_account_sid}:{s.twilio_auth_token}".encode()
        ).decode()

        url = _TWILIO_API.format(account_sid=s.twilio_account_sid)

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                data={"From": from_wa, "To": to_wa, "Body": body},
                headers={"Authorization": f"Basic {auth}"},
            )

        if not resp.is_success:
            err = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            code = err.get("code", resp.status_code)
            msg  = err.get("message", resp.text[:200])
            hint = ""
            if code == 63007:
                hint = " → WhatsApp sandbox not activated. Go to Twilio console → Messaging → Try it out → Send a WhatsApp message, then WhatsApp 'join <word>' to your sandbox number."
            elif code == 21608:
                hint = " → Recipient hasn't joined the sandbox. They must WhatsApp 'join <word>' to the sandbox number first."
            logger.error(
                "twilio_send_failed",
                status=resp.status_code,
                code=code,
                message=msg + hint,
                to=to_phone[-4:],
            )
        else:
            logger.info("twilio_message_queued", sid=resp.json().get("sid", "")[:8])


def _e164(phone: str) -> str:
    """Ensure phone is E.164 format (+91XXXXXXXXXX)."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        phone = "+91" + phone
    return phone
