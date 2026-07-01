from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _item_to_dict(item) -> dict:
    """Normalize an order item (typed object or dict) to a plain dict."""
    if isinstance(item, dict):
        return {
            "name":     item.get("name", ""),
            "quantity": float(item.get("quantity", 1)),
            "unit":     item.get("unit", "unit"),
            "category": item.get("category", "grocery"),
        }
    return {
        "name":     getattr(item, "name", ""),
        "quantity": float(getattr(item, "quantity", 1)),
        "unit":     getattr(item, "unit", "unit"),
        "category": getattr(item, "category", "grocery"),
    }


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    queue="pantry",
    name="app.tasks.pantry.update_pantry_post_order"
)
def update_pantry_post_order(self, household_id: str, swiggy_order_id: str):
    """Called 30 min after checkout. Fetches order details and updates pantry state."""
    import asyncio
    from app.database import AsyncSessionLocal
    from app.services.pantry_service import PantryService
    from app.services.auth_service import AuthService

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                from app.providers.factory import get_mcp_provider
                token = await AuthService(db).get_valid_token(household_id)
                mcp = get_mcp_provider(token)
                order_detail = await mcp.get_order_details(swiggy_order_id)
                # Normalize items — provider may return typed objects or plain dicts
                raw_items = order_detail.get("items", []) if isinstance(order_detail, dict) else getattr(order_detail, "items", [])
                order_items = [_item_to_dict(i) for i in raw_items]
                await PantryService(db).post_order_update(household_id, order_items)
                logger.info("pantry_updated", household_id=household_id, order_id=swiggy_order_id)
            except Exception as e:
                logger.error("pantry_update_failed", household_id=household_id, error=str(e))
                raise self.retry(exc=e)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()
