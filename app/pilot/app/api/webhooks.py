import hmac
import hashlib
from fastapi import APIRouter, Request, Header, HTTPException
from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_interakt_signature(body: bytes, signature: str) -> bool:
    """Validate Interakt webhook HMAC-SHA256 signature."""
    secret = settings.interakt_webhook_secret.encode()
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    x_interakt_signature: str = Header(default="")
):
    """Receive inbound WhatsApp messages from Interakt. Always returns 200."""
    body = await request.body()

    if not _verify_interakt_signature(body, x_interakt_signature):
        logger.warning("whatsapp_webhook_invalid_signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()

    # Process asynchronously — never block the webhook response
    from app.tasks.whatsapp import process_inbound_message
    process_inbound_message.delay(payload)

    logger.info("whatsapp_webhook_received", type=payload.get("type"))
    return {"received": True}
