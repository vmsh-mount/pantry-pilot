"""
PlanningService — orchestrator for the PantryPilot planning loop.

Responsibilities:
  1. create_loop_run()      — create a LoopRun record and return its ID
  2. run_loop()             — execute SENSE→PLAN→OPTIMIZE→CONFIRM stages via LangGraph
  3. run_place()            — execute PLACE stage after user confirms via WhatsApp
  4. handle_timeout()       — auto-skip if user doesn't respond within 6 hours
  5. reschedule_next_run()  — compute and persist next_run_at after completion
  6. mark_failed()          — mark a loop run as failed with reason

PlanningService does NOT contain stage logic — that lives in agent/planning_graph.py.
This service is the thin shell that wires Celery tasks, DB state, and the graph together.
"""

from datetime import datetime, timezone, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import LoopRun, Household, HouseholdPreferences
from app.utils.exceptions import HouseholdNotFoundError, HouseholdPausedError
from app.utils.logging import get_logger

logger = get_logger(__name__)


class PlanningService:
    """One instance per Celery task invocation, bound to a DB session."""

    def __init__(self, db: AsyncSession):
        self._db = db

    # ── 1. Create loop run ────────────────────────────────────────────────────

    async def create_loop_run(
        self,
        household_id: str,
        trigger_type: str = "scheduled",
    ) -> str:
        """
        Insert a new LoopRun record in 'pending' state.
        Returns the loop_run_id.
        Guards against running if household is paused or not onboarded.
        """
        hh_result = await self._db.execute(
            select(Household).where(Household.id == household_id)
        )
        household = hh_result.scalar_one_or_none()

        if household is None:
            raise HouseholdNotFoundError(f"Household {household_id} not found")

        if not household.onboarding_complete:
            raise HouseholdNotFoundError(f"Household {household_id} onboarding not complete")

        if household.is_paused:
            raise HouseholdPausedError(f"Household {household_id} is paused")

        loop_run = LoopRun(
            household_id  = household_id,
            trigger_type  = trigger_type,
            state         = "pending",
            triggered_at  = datetime.now(timezone.utc),
        )
        self._db.add(loop_run)
        await self._db.commit()
        await self._db.refresh(loop_run)

        logger.info(
            "loop_run_created",
            household_id = household_id,
            loop_run_id  = loop_run.id,
            trigger_type = trigger_type,
        )
        return loop_run.id

    # ── 2. Run the planning graph (SENSE → PLAN → OPTIMIZE → CONFIRM) ─────────

    async def run_loop(
        self,
        household_id: str,
        loop_run_id:  str,
        trigger_type: str = "scheduled",
    ) -> dict:
        """
        Execute the LangGraph planning graph.
        Returns the final state dict (useful for testing and debugging).

        The graph exits after CONFIRM — it sends the WhatsApp basket card and stops.
        PLACE is triggered separately by the Celery task when user confirms.
        """
        from app.agent.planning_graph import build_planning_graph
        from app.services.auth_service import AuthService

        # Decrypt token — lives only in memory during this invocation
        auth_svc     = AuthService(self._db)
        access_token = await auth_svc.get_valid_token(household_id)

        initial_state = {
            "household_id":  household_id,
            "loop_run_id":   loop_run_id,
            "access_token":  access_token,
            "trigger_type":  trigger_type,
            "should_abort":  False,
            "recent_orders": [],
        }

        graph       = build_planning_graph()
        final_state = await graph.ainvoke(initial_state)

        if final_state.get("should_abort"):
            # If the run was cleanly skipped (e.g. empty basket), don't overwrite with 'failed'
            if final_state.get("error_stage"):
                await self.mark_failed(
                    loop_run_id,
                    reason = final_state.get("error", "Unknown error"),
                    stage  = final_state.get("error_stage", "unknown"),
                )
                logger.error(
                    "loop_aborted",
                    household_id = household_id,
                    loop_run_id  = loop_run_id,
                    error        = final_state.get("error"),
                )
            else:
                logger.info(
                    "loop_aborted_cleanly",
                    household_id = household_id,
                    loop_run_id  = loop_run_id,
                )

        return final_state

    # ── 3. Run PLACE stage ───────────────────────────────────────────────────

    async def run_place(
        self,
        household_id: str,
        loop_run_id:  str,
    ) -> dict:
        """
        Execute the PLACE stage for a confirmed basket.
        Called by the Celery task when the user taps "✅ Looks good, order it".

        Reconstructs the state from the DB loop run + its items, then calls
        the place() node from planning_graph.py.
        """
        from app.agent.planning_graph import place
        from app.services.auth_service import AuthService
        from app.models.db import LoopRunItem

        # Re-fetch token (may have been refreshed since the loop ran)
        auth_svc     = AuthService(self._db)
        access_token = await auth_svc.get_valid_token(household_id)

        # Load LoopRun for address + slot
        run_result = await self._db.execute(
            select(LoopRun).where(LoopRun.id == loop_run_id)
        )
        loop_run = run_result.scalar_one_or_none()
        if loop_run is None:
            raise HouseholdNotFoundError(f"LoopRun {loop_run_id} not found")

        # Load household for WhatsApp + address
        hh_result = await self._db.execute(
            select(Household).where(Household.id == household_id)
        )
        household = hh_result.scalar_one_or_none()

        prefs_result = await self._db.execute(
            select(HouseholdPreferences).where(
                HouseholdPreferences.household_id == household_id
            )
        )
        prefs = prefs_result.scalar_one_or_none()

        # Load saved basket items from DB
        items_result = await self._db.execute(
            select(LoopRunItem).where(LoopRunItem.loop_run_id == loop_run_id)
        )
        loop_items = items_result.scalars().all()

        resolved_basket = [
            {
                "item_name":    i.item_name,
                "sku_id":       i.swiggy_sku_id,
                "product_name": i.swiggy_product_name,
                "brand":        i.brand,
                "category":     "grocery",
                "quantity":     float(i.quantity),
                "unit":         i.unit,
                "unit_price":   float(i.unit_price or 0),
                "total_price":  float(i.total_price or 0),
                "in_stock":     True,
                "added_by":     i.added_by,
                "is_substitution": i.is_substitution,
            }
            for i in loop_items
        ]

        estimated_total = sum(i["total_price"] for i in resolved_basket)

        # Resolve swiggy_address_id from our internal UUID FK
        swiggy_address_id = None
        if prefs and prefs.preferred_address_id:
            from app.models.db import Address
            addr_result = await self._db.execute(
                select(Address).where(Address.id == prefs.preferred_address_id)
            )
            addr = addr_result.scalar_one_or_none()
            if addr:
                swiggy_address_id = addr.swiggy_address_id

        # Build a minimal state for the place() node
        state = {
            "household_id":           household_id,
            "loop_run_id":            loop_run_id,
            "access_token":           access_token,
            "preferred_address_id":   str(prefs.preferred_address_id) if prefs else None,
            "swiggy_address_id":      swiggy_address_id,
            "preferred_delivery_slot": prefs.preferred_delivery_slot if prefs else "evening",
            "whatsapp_number":        household.whatsapp_number if household else "",
            "resolved_basket":        resolved_basket,
            "estimated_total":        estimated_total,
            "household_profile":      {
                "budget_max": household.weekly_budget_max if household else 2500,
            },
        }

        result = await place(state)

        if result.get("should_abort"):
            await self.mark_failed(
                loop_run_id,
                reason = result.get("error", "Placement failed"),
                stage  = "place",
            )

        return result

    # ── 4. Handle confirmation timeout ────────────────────────────────────────

    async def handle_timeout(
        self,
        household_id: str,
        loop_run_id:  str,
    ) -> None:
        """
        Called 6 hours after CONFIRM if the user hasn't responded.
        Auto-skips: marks the loop run as 'skipped', reschedules next run.
        """
        # Check if the run is still awaiting confirmation
        run_result = await self._db.execute(
            select(LoopRun).where(
                LoopRun.id    == loop_run_id,
                LoopRun.state == "awaiting_confirmation",
            )
        )
        loop_run = run_result.scalar_one_or_none()

        if loop_run is None:
            # Already completed/skipped by user — nothing to do
            logger.info(
                "timeout_no_action_needed",
                household_id = household_id,
                loop_run_id  = loop_run_id,
            )
            return

        loop_run.state       = "skipped"
        loop_run.skip_reason = "confirmation_timeout_6hr"
        await self._db.commit()

        # Schedule next run
        await self.reschedule_next_run(household_id)

        # Send gentle nudge (if within 24hr window from initial send)
        try:
            hh_result = await self._db.execute(
                select(Household).where(Household.id == household_id)
            )
            household = hh_result.scalar_one_or_none()
            if household and household.whatsapp_number:
                from app.services.whatsapp_service import WhatsAppService
                wa = WhatsAppService()
                await wa.send_text(
                    household.whatsapp_number,
                    "No worries — I've skipped this week's order 👍\n"
                    "Your next basket will be ready next week. "
                    "Reply *order now* anytime if you need groceries sooner.",
                )
        except Exception as e:
            logger.warning("timeout_whatsapp_failed", error=str(e))

        logger.info(
            "loop_run_timed_out",
            household_id = household_id,
            loop_run_id  = loop_run_id,
        )

    # ── 5. Reschedule next run ────────────────────────────────────────────────

    async def reschedule_next_run(self, household_id: str) -> datetime:
        """
        Compute and persist next_run_at based on preferred_order_day.
        Returns the scheduled datetime.
        """
        prefs_result = await self._db.execute(
            select(HouseholdPreferences).where(
                HouseholdPreferences.household_id == household_id
            )
        )
        prefs = prefs_result.scalar_one_or_none()

        day_name = prefs.preferred_order_day if prefs else "sunday"

        from app.services.onboarding_service import _next_weekday
        next_run = _next_weekday(day_name)

        if prefs:
            prefs.last_run_at = datetime.now(timezone.utc)
            prefs.next_run_at = next_run
            await self._db.commit()

        # Enqueue Celery task at ETA
        from app.tasks.planning import trigger_planning_loop
        trigger_planning_loop.apply_async(
            args   = [household_id],
            kwargs = {"trigger_type": "scheduled"},
            eta    = next_run,
        )

        logger.info(
            "next_run_scheduled",
            household_id = household_id,
            next_run_at  = next_run.isoformat(),
        )
        return next_run

    # ── 6. Mark failed ────────────────────────────────────────────────────────

    async def mark_failed(
        self,
        loop_run_id: str,
        reason:      str,
        stage:       str = "unknown",
    ) -> None:
        await self._db.execute(
            update(LoopRun)
            .where(LoopRun.id == loop_run_id)
            .values(
                state          = "failed",
                failure_reason = reason,
                failure_stage  = stage,
            )
        )
        await self._db.commit()
        logger.error("loop_run_failed", loop_run_id=loop_run_id, reason=reason, stage=stage)
