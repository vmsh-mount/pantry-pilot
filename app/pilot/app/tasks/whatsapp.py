"""
Celery task: process inbound WhatsApp messages.

Receives the raw Interakt webhook payload, parses it into an InboundMessage,
looks up the household by phone number, and dispatches to the appropriate
intent handler.

Intent handlers:
  opt_out          → mark whatsapp_opted_out=True, pause loop
  pause_loop       → set is_paused=True
  resume_loop      → set is_paused=False, schedule next run
  skip_week        → cancel pending loop run for this week
  confirm_order    → trigger order placement for pending loop run
  review_basket    → send full item list for editing
  remove_items     → remove items from pending basket
  add_item         → search Swiggy, add item to pending basket
  trigger_immediate→ kick off immediate planning run
  reauth           → send reauth URL
  send_help        → send help text
  unknown          → send fallback message
"""

import asyncio
from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

logger = get_logger(__name__)

_HELP_TEXT = (
    "Here's what you can say to PantryPilot:\n\n"
    "✅ *Looks good* — confirm this week's basket\n"
    "✏️ *Review items* — see full list and edit\n"
    "❌ *Skip this week* — cancel this cycle\n"
    "➕ *add milk* — add an item to the basket\n"
    "➖ *remove 3, 5* — remove items by number\n"
    "⏸ *pause* — pause your weekly grocery automation\n"
    "▶️ *resume* — resume paused automation\n"
    "🛒 *order now* — trigger an immediate basket\n"
    "❓ *help* — show this menu"
)

_FALLBACK_TEXT = (
    "I didn't quite get that. Reply *help* to see what I can do 😊"
)


@celery_app.task(
    queue       = "whatsapp",
    name        = "app.tasks.whatsapp.process_inbound_message",
    max_retries = 2,
    default_retry_delay = 30,
)
def process_inbound_message(payload: dict):
    """Route inbound WhatsApp message to correct handler."""
    asyncio.run(_handle(payload))


async def _handle(payload: dict) -> None:
    from sqlalchemy import select, update
    from app.database import AsyncSessionLocal
    from app.models.db import Household, HouseholdPreferences, LoopRun
    from app.services.whatsapp_service import WhatsAppService, parse_remove_indices, parse_add_item
    from app.config import get_settings

    wa       = WhatsAppService()
    settings = get_settings()

    msg = WhatsAppService.parse_inbound(payload)

    if not msg.phone_number:
        logger.warning("inbound_no_phone_number")
        return

    async with AsyncSessionLocal() as db:
        # Look up household by WhatsApp number
        result = await db.execute(
            select(Household).where(
                Household.whatsapp_number == msg.phone_number,
                Household.is_active       == True,
            )
        )
        household = result.scalar_one_or_none()

        if household is None:
            logger.warning("inbound_unknown_number", phone_last4=msg.phone_number[-4:])
            # Can't identify sender — silently drop (no reply to unknown numbers)
            return

        household_id = household.id
        logger.info("inbound_message", household_id=household_id, intent=msg.intent)

        # ── Intent handlers ───────────────────────────────────────────────────

        if msg.intent == "opt_out":
            await db.execute(
                update(Household)
                .where(Household.id == household_id)
                .values(whatsapp_opted_out=True, is_paused=True)
            )
            await db.commit()
            logger.info("household_opted_out", household_id=household_id)
            # Meta mandates no reply after STOP — we stay silent

        elif msg.intent == "pause_loop":
            from datetime import datetime, timezone
            await db.execute(
                update(Household)
                .where(Household.id == household_id)
                .values(
                    is_paused    = True,
                    paused_at    = datetime.now(timezone.utc),
                    paused_reason = "user_request_whatsapp",
                )
            )
            await db.commit()
            await wa.send_text(
                msg.phone_number,
                "Your weekly grocery automation is paused ⏸\n\n"
                "Reply *resume* anytime to start it again.",
            )

        elif msg.intent == "resume_loop":
            await db.execute(
                update(Household)
                .where(Household.id == household_id)
                .values(is_paused=False, paused_at=None, paused_reason=None)
            )
            await db.commit()

            # Schedule next run
            prefs_result = await db.execute(
                select(HouseholdPreferences).where(
                    HouseholdPreferences.household_id == household_id
                )
            )
            prefs = prefs_result.scalar_one_or_none()

            from app.tasks.planning import trigger_planning_loop
            from app.services.onboarding_service import _next_weekday
            next_run = _next_weekday(prefs.preferred_order_day if prefs else "sunday")
            trigger_planning_loop.apply_async(
                args   = [household_id],
                kwargs = {"trigger_type": "scheduled"},
                eta    = next_run,
            )
            await wa.send_text(
                msg.phone_number,
                f"You're back on track! ▶️\n\n"
                f"Your next grocery basket will be ready on "
                f"{next_run.strftime('%A, %d %b')}.",
            )

        elif msg.intent == "skip_week":
            # Cancel any pending loop run
            pending_result = await db.execute(
                select(LoopRun).where(
                    LoopRun.household_id == household_id,
                    LoopRun.state.in_(["pending", "awaiting_confirmation"]),
                )
            )
            pending = pending_result.scalars().all()
            for run in pending:
                run.state       = "skipped"
                run.skip_reason = "user_request_whatsapp"

            await db.commit()

            # Schedule next week
            prefs_result = await db.execute(
                select(HouseholdPreferences).where(
                    HouseholdPreferences.household_id == household_id
                )
            )
            prefs = prefs_result.scalar_one_or_none()
            from app.services.onboarding_service import _next_weekday
            next_run = _next_weekday(prefs.preferred_order_day if prefs else "sunday")

            from app.tasks.planning import trigger_planning_loop
            trigger_planning_loop.apply_async(
                args   = [household_id],
                kwargs = {"trigger_type": "scheduled"},
                eta    = next_run,
            )
            await wa.send_text(
                msg.phone_number,
                f"No problem! Skipping this week 👍\n\n"
                f"Your next basket will be ready on {next_run.strftime('%A, %d %b')}.\n"
                f"Reply *order now* anytime if you change your mind.",
            )

        elif msg.intent == "confirm_order":
            # Find the awaiting run and place it directly — don't re-plan
            run_result = await db.execute(
                select(LoopRun).where(
                    LoopRun.household_id == household_id,
                    LoopRun.state        == "awaiting_confirmation",
                ).limit(1)
            )
            pending_run = run_result.scalar_one_or_none()
            if pending_run is None:
                await wa.send_text(
                    msg.phone_number,
                    "No basket is waiting for confirmation right now.\n"
                    "Reply *order now* to generate a new one.",
                )
            else:
                from app.tasks.planning import place_confirmed_order
                from datetime import datetime, timezone
                pending_run.state               = "confirmed"
                pending_run.user_action         = "confirmed"
                pending_run.confirm_responded_at = datetime.now(timezone.utc)
                await db.commit()
                place_confirmed_order.delay(household_id, pending_run.id)
                await wa.send_text(
                    msg.phone_number,
                    "Placing your order now... 🛒\nI'll send you a confirmation once it's placed!",
                )

        elif msg.intent == "review_basket":
            # Fetch the pending basket and send full item list
            pending_result = await db.execute(
                select(LoopRun).where(
                    LoopRun.household_id == household_id,
                    LoopRun.state        == "awaiting_confirmation",
                )
            )
            pending_run = pending_result.scalar_one_or_none()

            if pending_run is None:
                await wa.send_text(
                    msg.phone_number,
                    "No basket is waiting for review right now.\n"
                    "Reply *order now* to generate a new one.",
                )
                return

            from app.models.db import LoopRunItem
            from app.services.whatsapp_service import build_full_item_list
            items_result = await db.execute(
                select(LoopRunItem).where(LoopRunItem.loop_run_id == pending_run.id)
            )
            items = items_result.scalars().all()
            item_dicts = [
                {"item_name": i.item_name, "total_price": float(i.total_price or 0)}
                for i in items
            ]
            item_list = build_full_item_list(item_dicts)
            await wa.send_text(
                msg.phone_number,
                f"Here's your full basket:\n\n{item_list}\n\n"
                "Reply *remove 3, 5* to remove items by number\n"
                "or *add [item name]* to add something.",
                buttons=[
                    {"id": "btn_confirm", "title": "✅ Order it"},
                    {"id": "btn_skip",    "title": "❌ Skip this week"},
                ],
            )

        elif msg.intent == "trigger_immediate":
            from app.tasks.planning import trigger_planning_loop
            trigger_planning_loop.apply_async(
                args   = [household_id],
                kwargs = {"trigger_type": "user_immediate_whatsapp"},
            )
            await wa.send_text(
                msg.phone_number,
                "On it! I'm building your basket now 🛒\n"
                "I'll send it over in a few seconds.",
            )

        elif msg.intent == "reauth":
            reauth_url = f"https://pantrypilot.in/reauth?hid={household_id}"
            await wa.send_text(
                msg.phone_number,
                f"To reconnect your Swiggy account, visit:\n{reauth_url}\n\n"
                "It takes less than a minute 🔗",
            )

        elif msg.intent == "remove_items":
            indices = parse_remove_indices(msg.text)
            if not indices:
                await wa.send_text(
                    msg.phone_number,
                    "To remove items, reply with their numbers, e.g. *remove 3, 5*",
                )
                return

            pending_result = await db.execute(
                select(LoopRun).where(
                    LoopRun.household_id == household_id,
                    LoopRun.state        == "awaiting_confirmation",
                )
            )
            pending_run = pending_result.scalar_one_or_none()

            if pending_run is None:
                await wa.send_text(
                    msg.phone_number,
                    "No basket is waiting for edits right now.",
                )
                return

            from app.models.db import LoopRunItem, LoopRunEdit
            items_result = await db.execute(
                select(LoopRunItem).where(LoopRunItem.loop_run_id == pending_run.id)
            )
            all_items = items_result.scalars().all()

            removed_names = []
            for idx in sorted(set(indices), reverse=True):
                if 0 <= idx < len(all_items):
                    item = all_items[idx]
                    removed_names.append(item.item_name)
                    db.add(LoopRunEdit(
                        loop_run_id  = pending_run.id,
                        household_id = household_id,
                        edit_type    = "remove_item",
                        item_name    = item.item_name,
                        original_qty = float(item.quantity),
                    ))
                    await db.delete(item)

            await db.commit()

            if removed_names:
                removed_str = "\n".join(f"— {n}" for n in removed_names)
                await wa.send_text(
                    msg.phone_number,
                    f"Removed:\n{removed_str}\n\n"
                    "Reply *order now* to confirm, or keep editing.",
                    buttons=[
                        {"id": "btn_confirm", "title": "✅ Order it"},
                        {"id": "btn_review",  "title": "✏️ Review basket"},
                    ],
                )
            else:
                await wa.send_text(
                    msg.phone_number,
                    "Those item numbers weren't in the basket. "
                    "Reply *review* to see the full list.",
                )

        elif msg.intent == "add_item":
            item_query = parse_add_item(msg.text)
            if not item_query:
                await wa.send_text(
                    msg.phone_number,
                    "To add an item, say *add [item name]*, e.g. *add amul butter*",
                )
                return
            # Delegate to planning task which has the MCP token
            from app.tasks.planning import add_item_to_basket
            add_item_to_basket.apply_async(
                args = [household_id, item_query, msg.phone_number]
            )
            await wa.send_text(
                msg.phone_number,
                f"Looking for *{item_query}* on Swiggy... 🔍\nI'll update you in a moment.",
            )

        elif msg.intent == "send_help":
            await wa.send_text(msg.phone_number, _HELP_TEXT)

        else:  # unknown
            await wa.send_text(msg.phone_number, _FALLBACK_TEXT)
