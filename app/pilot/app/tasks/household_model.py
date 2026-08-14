from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="planning",
    name="app.tasks.household_model.update_household_model",
)
def update_household_model(self, household_id: str):
    """
    Refresh household_models (learned brand/item preferences) after an
    order. Not blocking, best-effort — failures are logged, not retried
    aggressively, same as the update this replaced.

    Previously fired via background_tasks.add_task(_run_model_update, ...)
    inside api/quick.py's POST /checkout route body — FastAPI's supported,
    GC-safe mechanism for exactly this ("run after the response, don't
    block on it"). That mechanism needs a BackgroundTasks instance, which
    only exists via dependency injection into a route bound to a live
    Request. quick_checkout.checkout() is now a plain service function
    called from two places — the REST route (which has that Request) and
    the AI assistant's checkout_basket tool (which doesn't; it's not a
    route at all) — so it structurally can't keep depending on
    BackgroundTasks. Routing through Celery instead needs no request
    context, matching the other two post-checkout dispatches already in
    that function (update_pantry_post_order, dispatch_post_order_tasks).

    (A bare asyncio.create_task() was tried as a simpler fix during this
    same change and rejected — an unreferenced task risks being garbage-
    collected mid-execution, and it measurably reintroduced the exact
    cross-event-loop connection problem this task exists to avoid. Not
    what the original code did, just the wrong alternative to this one.)
    """
    import asyncio

    async def _run():
        from app.database import AsyncSessionLocal
        from app.services.household_model_service import update_model
        try:
            async with AsyncSessionLocal() as db:
                await update_model(household_id, loop_run_id=None, db=db)
                await db.commit()
        except Exception as e:
            logger.warning("household_model_update_failed", household_id=household_id, error=str(e))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()
