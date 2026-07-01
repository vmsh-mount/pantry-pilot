"""
InteraktWhatsAppProvider — sends all outbound WhatsApp messages via Interakt BSP.

Extracted from app/services/whatsapp_service.py. Implements the WhatsAppProvider
protocol. All template-sending logic lives here; WhatsAppService delegates to this.
"""

import base64
import logging
from typing import Optional

import httpx

from app.config import get_settings

_log = logging.getLogger("providers.whatsapp.interakt")
_INTERAKT_URL = "https://api.interakt.ai/v1/public/message/"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

_DEFAULT_COUNTRY_CODE = "+91"


def _split_phone(phone: str) -> tuple[str, str]:
    if phone.startswith("+91") and len(phone) > 3:
        return "+91", phone[3:]
    if phone.startswith("+") and len(phone) > 1:
        return phone[:3], phone[3:]
    return _DEFAULT_COUNTRY_CODE, phone


class InteraktWhatsAppProvider:
    """Sends WhatsApp messages via the Interakt BSP API."""

    async def send_otp(self, phone: str, otp: str) -> None:
        await self._send_template(
            phone_number=phone,
            template_name="pantrypilot_otp_verification",
            body_values=[otp],
        )
        _log.info("whatsapp_otp_sent", extra={"phone_last4": phone[-4:]})

    async def send_basket_preview(
        self,
        phone: str,
        summary: str,
        total: float,
        budget: float,
    ) -> None:
        await self._send_template(
            phone_number=phone,
            template_name="pantrypilot_basket_preview",
            body_values=[summary, str(int(total)), str(int(budget))],
            buttons=[
                {"id": "btn_confirm", "title": "Looks good, order it"},
                {"id": "btn_review", "title": "Let me review items"},
                {"id": "btn_skip", "title": "Skip this week"},
            ],
        )
        _log.info("whatsapp_basket_preview_sent", extra={"phone_last4": phone[-4:], "total": total})

    async def send_order_receipt(
        self,
        phone: str,
        item_count: int,
        total: float,
        area: str,
        eta: str,
        order_id: str,
    ) -> None:
        await self._send_template(
            phone_number=phone,
            template_name="pantrypilot_order_receipt",
            body_values=[str(item_count), str(int(total)), area, eta],
            buttons=[
                {
                    "id": "btn_track",
                    "title": "Track on Swiggy",
                    "type": "url",
                    "url": f"https://www.swiggy.com/order/{order_id}",
                }
            ],
        )
        _log.info("whatsapp_order_receipt_sent", extra={"phone_last4": phone[-4:], "total": total})

    async def send_reauth_48hr(self, phone: str, expiry_label: str, reauth_url: str) -> None:
        await self._send_template(
            phone_number=phone,
            template_name="pantrypilot_reauth_48hr",
            body_values=[expiry_label],
            buttons=[{"id": "btn_reauth", "title": "Reconnect Swiggy", "type": "url", "url": reauth_url}],
        )
        _log.info("whatsapp_reauth_48hr_sent", extra={"phone_last4": phone[-4:]})

    async def send_reauth_24hr(self, phone: str, expiry_label: str, reauth_url: str) -> None:
        await self._send_template(
            phone_number=phone,
            template_name="pantrypilot_reauth_24hr",
            body_values=[expiry_label],
            buttons=[{"id": "btn_reauth_urgent", "title": "Reconnect now", "type": "url", "url": reauth_url}],
        )
        _log.info("whatsapp_reauth_24hr_sent", extra={"phone_last4": phone[-4:]})

    async def send_session_expired(self, phone: str, reauth_url: str) -> None:
        await self._send_template(
            phone_number=phone,
            template_name="pantrypilot_session_expired",
            body_values=[],
            buttons=[{"id": "btn_reauth_expired", "title": "Reconnect Swiggy", "type": "url", "url": reauth_url}],
        )
        _log.info("whatsapp_session_expired_sent", extra={"phone_last4": phone[-4:]})

    async def send_text(
        self,
        phone: str,
        text: str,
        buttons: Optional[list] = None,
    ) -> None:
        country_code, local = _split_phone(phone)
        payload: dict = {
            "countryCode": country_code,
            "phoneNumber": local,
            "callbackData": "",
            "type": "Text",
            "data": {"message": text},
        }

        if buttons:
            payload["type"] = "InteractiveMessage"
            payload["data"] = {
                "type": "button",
                "body": {"text": text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                        for b in buttons
                    ]
                },
            }

        await self._post(payload)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _send_template(
        self,
        phone_number: str,
        template_name: str,
        body_values: list[str],
        buttons: Optional[list[dict]] = None,
    ) -> None:
        country_code, local = _split_phone(phone_number)

        template: dict = {
            "name": template_name,
            "languageCode": "en",
            "bodyValues": body_values,
        }

        if buttons:
            btn_components = []
            for idx, b in enumerate(buttons):
                if b.get("type") == "url":
                    btn_components.append({
                        "type": "button",
                        "sub_type": "url",
                        "index": str(idx),
                        "parameters": [{"type": "text", "text": b.get("url", "")}],
                    })
                else:
                    btn_components.append({
                        "type": "button",
                        "sub_type": "quick_reply",
                        "index": str(idx),
                        "parameters": [{"type": "payload", "payload": b.get("id", "")}],
                    })
            template["components"] = btn_components

        payload = {
            "countryCode": country_code,
            "phoneNumber": local,
            "callbackData": "",
            "type": "Template",
            "template": template,
        }

        await self._post(payload)

    async def _post(self, payload: dict) -> None:
        settings = get_settings()
        api_key = settings.interakt_api_key
        auth_header = base64.b64encode(f"{api_key}:".encode()).decode()

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _INTERAKT_URL,
                json=payload,
                headers={
                    "Authorization": f"Basic {auth_header}",
                    "Content-Type": "application/json",
                },
            )

        if not resp.is_success:
            _log.error(
                "interakt_send_failed",
                extra={"status": resp.status_code, "body": resp.text[:200]},
            )
