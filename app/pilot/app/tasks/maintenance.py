from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(queue="maintenance", name="app.tasks.maintenance.check_token_expiry")
def check_token_expiry():
    """Runs daily at 9 AM IST. Sends re-auth reminders for expiring tokens."""
    import asyncio
    from app.database import AsyncSessionLocal
    from app.services.auth_service import AuthService
    from app.services.whatsapp_service import WhatsAppService

    async def _run():
        async with AsyncSessionLocal() as db:
            from datetime import datetime, timezone
            from sqlalchemy import select as sa_select
            from app.models.db import Household

            expiring = await AuthService(db).get_expiring_tokens()
            wa       = WhatsAppService()

            for token_record in expiring:
                hours_remaining = (
                    token_record.token_expiry - datetime.now(timezone.utc)
                ).total_seconds() / 3600

                # Fetch phone number for this household
                hh_result = await db.execute(
                    sa_select(Household).where(Household.id == token_record.household_id)
                )
                household = hh_result.scalar_one_or_none()
                phone = household.whatsapp_number if household else None
                if not phone:
                    continue

                reauth_url = f"https://pantrypilot.in/reauth?hid={token_record.household_id}"
                expiry_label = token_record.token_expiry.strftime("%A, %d %b at %I %p")

                if hours_remaining <= 24 and not token_record.nudge_24hr_sent:
                    await wa.send_reauth_24hr(phone, expiry_label, reauth_url)
                    await AuthService(db).mark_nudge_sent(token_record.household_id, "24hr")
                    logger.info("reauth_nudge_sent", household_id=token_record.household_id, urgency="24hr")

                elif hours_remaining <= 48 and not token_record.nudge_48hr_sent:
                    await wa.send_reauth_48hr(phone, expiry_label, reauth_url)
                    await AuthService(db).mark_nudge_sent(token_record.household_id, "48hr")
                    logger.info("reauth_nudge_sent", household_id=token_record.household_id, urgency="48hr")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


@celery_app.task(queue="maintenance", name="app.tasks.maintenance.catchup_missed_runs")
def catchup_missed_runs():
    """Runs every hour. Catches planning loops that were missed (e.g. system downtime)."""
    import asyncio
    from datetime import datetime, timezone, timedelta
    from app.database import AsyncSessionLocal
    from app.services.planning_service import PlanningService
    from app.tasks.planning import trigger_planning_loop

    async def _run():
        async with AsyncSessionLocal() as db:
            from app.services.household_service import HouseholdService
            household_svc = HouseholdService(db)
            missed = await household_svc.get_missed_runs(
                before=datetime.now(timezone.utc) - timedelta(minutes=30)
            )
            for household_id in missed:
                trigger_planning_loop.delay(household_id)
                logger.info("catchup_run_triggered", household_id=household_id)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()
