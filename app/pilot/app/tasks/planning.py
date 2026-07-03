"""
Celery planning tasks.

  trigger_planning_loop    — main entry point, called by Beat + WhatsApp handler
  handle_confirmation_timeout — auto-skip after 6hr no response
  add_item_to_basket       — add a single item mid-review (via WhatsApp "add X")
"""

import asyncio
from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    queue="planning",
    name="app.tasks.planning.trigger_planning_loop",
)
def trigger_planning_loop(
    self,
    household_id: str,
    trigger_type: str = "scheduled",
):
    """Main planning loop task. Triggered by Celery Beat per household schedule."""

    async def _run():
        from app.database import AsyncSessionLocal
        from app.services.planning_service import PlanningService
        from app.services.auth_service import AuthService
        from app.services.whatsapp_service import WhatsAppService
        from app.utils.exceptions import (
            TokenExpiredError, HouseholdPausedError, HouseholdNotFoundError,
        )

        async with AsyncSessionLocal() as db:
            try:
                # Guard: verify token before doing anything
                auth = AuthService(db)
                await auth.get_valid_token(household_id)

                planning     = PlanningService(db)
                loop_run_id  = await planning.create_loop_run(household_id, trigger_type)
                await planning.run_loop(household_id, loop_run_id, trigger_type)
                # Next run is scheduled inside run_loop → confirm node

                logger.info(
                    "planning_loop_complete",
                    household_id = household_id,
                    loop_run_id  = loop_run_id,
                )

            except TokenExpiredError:
                logger.warning("planning_loop_token_expired", household_id=household_id)
                await _handle_token_expired(db, household_id)

            except (HouseholdPausedError, HouseholdNotFoundError) as e:
                logger.warning(
                    "planning_loop_skipped",
                    household_id = household_id,
                    reason       = str(e),
                )

            except Exception as e:
                logger.error(
                    "planning_loop_failed",
                    household_id = household_id,
                    error        = str(e),
                )
                raise self.retry(exc=e)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


@celery_app.task(
    queue="planning",
    name="app.tasks.planning.handle_confirmation_timeout",
)
def handle_confirmation_timeout(household_id: str, loop_run_id: str):
    """Auto-skip if user hasn't responded within 6 hours."""

    async def _run():
        from app.database import AsyncSessionLocal
        from app.services.planning_service import PlanningService

        async with AsyncSessionLocal() as db:
            planning = PlanningService(db)
            await planning.handle_timeout(household_id, loop_run_id)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


@celery_app.task(
    queue="planning",
    name="app.tasks.planning.place_confirmed_order",
    max_retries=1,
)
def place_confirmed_order(household_id: str, loop_run_id: str):
    """Execute the PLACE stage after the user confirms via the dashboard or WhatsApp."""

    async def _run():
        from app.database import AsyncSessionLocal
        from app.services.planning_service import PlanningService

        async with AsyncSessionLocal() as db:
            planning = PlanningService(db)
            await planning.run_place(household_id, loop_run_id)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


@celery_app.task(
    queue="planning",
    name="app.tasks.planning.add_item_to_basket",
    max_retries=1,
)
def add_item_to_basket(household_id: str, item_query: str, phone_number: str):
    """
    Search Swiggy for `item_query` and append to the pending basket.
    Called by the WhatsApp inbound handler when user says "add milk".
    """

    async def _run():
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models.db import LoopRun
        from app.services.whatsapp_service import WhatsAppService
        from app.services.basket_editing_service import BasketEditingService
        from app.utils.exceptions import SwiggyMCPError, TokenExpiredError

        async with AsyncSessionLocal() as db:
            wa  = WhatsAppService()
            svc = BasketEditingService()

            # Find pending loop run
            run_result = await db.execute(
                select(LoopRun).where(
                    LoopRun.household_id == household_id,
                    LoopRun.state        == "awaiting_confirmation",
                )
            )
            loop_run = run_result.scalar_one_or_none()
            if loop_run is None:
                await wa.send_text(
                    phone_number,
                    "No basket is waiting for edits right now.\n"
                    "Reply *order now* to generate a new one.",
                )
                return

            try:
                products = await svc.search_items(db, household_id, item_query, limit=3)
            except TokenExpiredError:
                await wa.send_text(
                    phone_number,
                    "⚠️ Your Swiggy session has expired. Please reconnect to add items.",
                )
                return
            except SwiggyMCPError:
                await wa.send_text(
                    phone_number,
                    f"Sorry, I couldn't search for *{item_query}* right now. Try again in a moment.",
                )
                return

            if not products:
                await wa.send_text(
                    phone_number,
                    f"*{item_query}* doesn't seem to be available on Swiggy Instamart right now.",
                )
                return

            product  = products[0]
            new_item = await svc.add_item(
                db, loop_run, household_id, product,
                item_query=item_query,
                add_reason="User requested via WhatsApp",
            )

            from app.services.basket_editing_service import _pattr
            pname  = _pattr(product, "name", item_query) or item_query
            pprice = _pattr(product, "price", 0) or 0
            await wa.send_text(
                phone_number,
                f"Added *{pname}* — ₹{int(pprice)}\n\n"
                "Reply *review* to see the full basket, or *order now* to confirm.",
            )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


# ── Private helpers ───────────────────────────────────────────────────────────

async def _handle_token_expired(db, household_id: str) -> None:
    """Pause household and send re-auth WhatsApp alert."""
    from datetime import datetime, timezone
    from sqlalchemy import select, update as sa_update
    from app.models.db import Household
    from app.services.whatsapp_service import WhatsAppService
    from app.config import get_settings

    settings = get_settings()

    await db.execute(
        sa_update(Household)
        .where(Household.id == household_id)
        .values(
            is_paused    = True,
            paused_at    = datetime.now(timezone.utc),
            paused_reason = "token_expired",
        )
    )
    await db.commit()

    hh_result = await db.execute(
        select(Household).where(Household.id == household_id)
    )
    household = hh_result.scalar_one_or_none()

    if household and household.whatsapp_number:
        reauth_url = f"https://pantrypilot.in/reauth?hid={household_id}"
        wa = WhatsAppService()
        await wa.send_session_expired(household.whatsapp_number, reauth_url)
