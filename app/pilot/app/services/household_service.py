"""
HouseholdService — lifecycle management for a household record.

Responsibilities:
  - get_settings()    — return household + preferences as a settings dict
  - pause()           — suspend the planning loop
  - resume()          — re-activate and schedule next run
  - delete()          — GDPR/DPDP-compliant full data erasure
  - get_missed_runs() — return household IDs whose next_run_at is overdue
                        (used by the maintenance catchup task)

Called by:
  - settings_router.py  (GET/POST /v1/settings/*)
  - tasks/planning.py   (_handle_token_expired, handle_confirmation_timeout)
  - tasks/whatsapp.py   (pause/resume intents)
"""

from datetime import datetime, timezone

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    Household, HouseholdPreferences, HouseholdMember,
    SwiggyToken, Address, BrandPreference,
    PantryItem, LoopRun, Order, OrderItem, LoopRunItem, LoopRunEdit,
)
from app.utils.exceptions import HouseholdNotFoundError
from app.utils.logging import get_logger

logger = get_logger(__name__)


class HouseholdService:
    """One instance per request, bound to a DB session."""

    def __init__(self, db: AsyncSession):
        self._db = db

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_settings(self, household_id: str) -> dict:
        """
        Return a settings dict for the cockpit settings page.
        Includes: household profile, preferences, WhatsApp status, pause state.
        """
        hh_result = await self._db.execute(
            select(Household).where(Household.id == household_id)
        )
        household = hh_result.scalar_one_or_none()
        if household is None:
            raise HouseholdNotFoundError(f"Household {household_id} not found")

        prefs_result = await self._db.execute(
            select(HouseholdPreferences).where(
                HouseholdPreferences.household_id == household_id
            )
        )
        prefs = prefs_result.scalar_one_or_none()

        return {
            "household_id":        household_id,
            "household_type":      household.household_type,
            "member_count":        household.member_count,
            "diet_type":           household.diet_type,
            "allergies":           household.allergies or [],
            "city":                household.city,
            "weekly_budget_min":   household.weekly_budget_min,
            "weekly_budget_max":   household.weekly_budget_max,
            "whatsapp_number":     household.whatsapp_number,
            "whatsapp_verified":   household.whatsapp_verified,
            "whatsapp_opted_out":  household.whatsapp_opted_out,
            "onboarding_complete": household.onboarding_complete,
            "is_active":           household.is_active,
            "is_paused":           household.is_paused,
            "paused_at":           household.paused_at.isoformat() if household.paused_at else None,
            "paused_reason":       household.paused_reason,
            "preferences": {
                "preferred_order_day":      prefs.preferred_order_day if prefs else "sunday",
                "preferred_delivery_slot":  prefs.preferred_delivery_slot if prefs else "evening",
                "confirmation_window_hrs":  prefs.confirmation_window_hrs if prefs else 4,
                "next_run_at":              prefs.next_run_at.isoformat() if prefs and prefs.next_run_at else None,
                "last_run_at":              prefs.last_run_at.isoformat() if prefs and prefs.last_run_at else None,
                "freq_staples":             prefs.freq_staples if prefs else "weekly",
                "freq_fresh_produce":       prefs.freq_fresh_produce if prefs else "weekly",
                "freq_dairy_eggs":          prefs.freq_dairy_eggs if prefs else "weekly",
                "freq_packaged":            prefs.freq_packaged if prefs else "weekly",
            } if prefs else {},
        }

    async def update_settings(
        self,
        household_id: str,
        updates: dict,
    ) -> dict:
        """
        Patch household + preferences fields.
        Only fields present in `updates` are changed.
        Returns the new settings dict.
        """
        household_fields   = {
            "household_type", "member_count", "diet_type",
            "allergies", "weekly_budget_min", "weekly_budget_max",
        }
        preference_fields  = {
            "preferred_order_day", "preferred_delivery_slot",
            "confirmation_window_hrs",
            "freq_staples", "freq_fresh_produce", "freq_dairy_eggs", "freq_packaged",
        }

        hh_updates   = {k: v for k, v in updates.items() if k in household_fields}
        pref_updates = {k: v for k, v in updates.items() if k in preference_fields}

        if hh_updates:
            await self._db.execute(
                update(Household)
                .where(Household.id == household_id)
                .values(**hh_updates)
            )

        if pref_updates:
            prefs_result = await self._db.execute(
                select(HouseholdPreferences).where(
                    HouseholdPreferences.household_id == household_id
                )
            )
            prefs = prefs_result.scalar_one_or_none()
            if prefs:
                for k, v in pref_updates.items():
                    setattr(prefs, k, v)
            else:
                self._db.add(HouseholdPreferences(
                    household_id=household_id, **pref_updates
                ))

        await self._db.commit()
        logger.info("settings_updated", household_id=household_id, fields=list(updates.keys()))
        return await self.get_settings(household_id)

    # ── Pause ─────────────────────────────────────────────────────────────────

    async def pause(self, household_id: str, reason: str = "user_request") -> None:
        """
        Suspend the planning loop for this household.
        - Sets is_paused=True with timestamp and reason
        - Cancels any pending LoopRun in 'pending' state
        """
        await self._db.execute(
            update(Household)
            .where(Household.id == household_id)
            .values(
                is_paused     = True,
                paused_at     = datetime.now(timezone.utc),
                paused_reason = reason,
            )
        )

        # Cancel pending (not-yet-started) loop runs
        await self._db.execute(
            update(LoopRun)
            .where(
                LoopRun.household_id == household_id,
                LoopRun.state        == "pending",
            )
            .values(
                state       = "skipped",
                skip_reason = f"household_paused:{reason}",
            )
        )

        await self._db.commit()
        logger.info("household_paused", household_id=household_id, reason=reason)

    # ── Resume ────────────────────────────────────────────────────────────────

    async def resume(self, household_id: str) -> datetime:
        """
        Re-activate the household and schedule the next planning loop.
        Returns the next_run_at datetime.
        """
        await self._db.execute(
            update(Household)
            .where(Household.id == household_id)
            .values(
                is_paused     = False,
                paused_at     = None,
                paused_reason = None,
            )
        )
        await self._db.commit()

        # Delegate scheduling to PlanningService to avoid duplication
        from app.services.planning_service import PlanningService
        planning  = PlanningService(self._db)
        next_run  = await planning.reschedule_next_run(household_id)

        logger.info(
            "household_resumed",
            household_id = household_id,
            next_run_at  = next_run.isoformat(),
        )
        return next_run

    # ── Delete (full DPDP erasure) ────────────────────────────────────────────

    async def delete(self, household_id: str) -> None:
        """
        Hard-delete all data for a household in dependency order.
        Cascades are set on FK relationships, but we delete explicitly
        for audit clarity and to handle tables without CASCADE.

        Order: order_items → orders → loop_run_edits → loop_run_items →
               loop_runs → pantry_items → brand_preferences → addresses →
               swiggy_tokens → household_members → household_preferences →
               household
        """
        # Collect order IDs first
        order_ids_result = await self._db.execute(
            select(Order.id).where(Order.household_id == household_id)
        )
        order_ids = [row[0] for row in order_ids_result.all()]

        if order_ids:
            await self._db.execute(
                delete(OrderItem).where(OrderItem.order_id.in_(order_ids))
            )

        # Collect loop run IDs
        run_ids_result = await self._db.execute(
            select(LoopRun.id).where(LoopRun.household_id == household_id)
        )
        run_ids = [row[0] for row in run_ids_result.all()]

        if run_ids:
            await self._db.execute(
                delete(LoopRunEdit).where(LoopRunEdit.loop_run_id.in_(run_ids))
            )
            await self._db.execute(
                delete(LoopRunItem).where(LoopRunItem.loop_run_id.in_(run_ids))
            )

        # Delete by household_id directly for remaining tables
        for Model in (
            Order, LoopRun, PantryItem, BrandPreference,
            Address, SwiggyToken, HouseholdMember, HouseholdPreferences,
        ):
            await self._db.execute(
                delete(Model).where(Model.household_id == household_id)
            )

        await self._db.execute(
            delete(Household).where(Household.id == household_id)
        )

        await self._db.commit()
        logger.info("household_deleted", household_id=household_id)

    # ── Missed run detection (used by maintenance task) ───────────────────────

    async def get_missed_runs(self, before: datetime) -> list[str]:
        """
        Return household IDs whose next_run_at is before `before` and
        the household is active, not paused, and onboarding is complete.
        Used by the hourly catchup maintenance task.
        """
        result = await self._db.execute(
            select(HouseholdPreferences.household_id)
            .join(Household, Household.id == HouseholdPreferences.household_id)
            .where(
                HouseholdPreferences.next_run_at <= before,
                Household.is_active           == True,
                Household.is_paused           == False,
                Household.onboarding_complete == True,
            )
        )
        household_ids = [row[0] for row in result.all()]
        logger.info("missed_runs_found", count=len(household_ids))
        return household_ids
