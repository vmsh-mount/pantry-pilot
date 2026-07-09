"""
Routines domain logic — create, read, update, pause, resume, skip, delete,
and next-run-at computation.
"""
import json
import math
from datetime import datetime, timedelta, timezone, time as dt_time
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.db import Routine, RoutineItem, RoutineRun
from app.schemas.routines import RoutineCreate, RoutinePatch, RoutineOut, RoutineItemOut, RoutineRunOut

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

# ── Time helpers ──────────────────────────────────────────────────────────────

def _ist_hhmm_to_utc_time(hhmm: str) -> dt_time:
    """Convert "HH:MM" IST string to a UTC time object (IST = UTC+5:30)."""
    h, m = map(int, hhmm.split(":"))
    ist_minutes = h * 60 + m
    utc_minutes = (ist_minutes - 330) % (24 * 60)
    return dt_time(utc_minutes // 60, utc_minutes % 60, tzinfo=UTC)


def _utc_time_to_ist_hhmm(t: dt_time) -> str:
    """Convert stored UTC time to IST HH:MM string for display."""
    utc_minutes = t.hour * 60 + t.minute
    ist_minutes = (utc_minutes + 330) % (24 * 60)
    return f"{ist_minutes // 60:02d}:{ist_minutes % 60:02d}"


def _combine_utc(date: datetime, t: dt_time) -> datetime:
    """Combine a date (from a datetime) with a UTC time to get a UTC timestamp."""
    return datetime(
        date.year, date.month, date.day,
        t.hour, t.minute, 0, tzinfo=UTC,
    )


def _as_utc(dt: datetime) -> datetime:
    """Return dt as UTC-aware; attach UTC if naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def compute_next_run_at(routine: Routine, after: datetime | None = None) -> datetime | None:
    """
    Compute the next UTC timestamp for this routine after `after` (default: now).
    Returns None if end_date is already passed.
    """
    now = after or datetime.now(UTC)
    sched_time = routine.schedule_time  # stored UTC time

    if routine.frequency_type == "every_n_days":
        candidate = _combine_utc(now, sched_time)
        if candidate <= now:
            candidate += timedelta(days=routine.frequency_value)
        else:
            # snap to the correct grid from start_date
            start = _combine_utc(routine.start_date, sched_time)
            delta_days = routine.frequency_value
            steps = math.ceil((now - start).total_seconds() / (delta_days * 86400))
            candidate = start + timedelta(days=steps * delta_days)
            if candidate <= now:
                candidate += timedelta(days=delta_days)

    elif routine.frequency_type == "weekly":
        target_weekday = routine.frequency_value  # 0=Mon, 6=Sun (date.weekday())
        days_ahead = (target_weekday - now.weekday()) % 7
        if days_ahead == 0:
            candidate = _combine_utc(now, sched_time)
            if candidate <= now:
                days_ahead = 7
                candidate = _combine_utc(now + timedelta(days=days_ahead), sched_time)
        else:
            candidate = _combine_utc(now + timedelta(days=days_ahead), sched_time)

    elif routine.frequency_type == "monthly":
        from dateutil.relativedelta import relativedelta
        target_day = routine.frequency_value  # 1–28
        candidate = _combine_utc(now.replace(day=target_day), sched_time)
        if candidate <= now:
            next_month = now + relativedelta(months=1)
            candidate = _combine_utc(next_month.replace(day=target_day), sched_time)
    else:
        return None

    if routine.end_date and candidate > _as_utc(routine.end_date):
        return None
    return candidate


def _upcoming_runs(routine: Routine, count: int = 5) -> list[datetime]:
    """Generate the next `count` run timestamps from next_run_at."""
    from dateutil.relativedelta import relativedelta
    results: list[datetime] = []
    cursor = routine.next_run_at
    if not cursor:
        return results

    sched_time = routine.schedule_time

    end_utc = _as_utc(routine.end_date) if routine.end_date else None
    for _ in range(count):
        if end_utc and cursor > end_utc:
            break
        results.append(cursor)

        if routine.frequency_type == "every_n_days":
            cursor = cursor + timedelta(days=routine.frequency_value)
        elif routine.frequency_type == "weekly":
            cursor = cursor + timedelta(weeks=1)
        elif routine.frequency_type == "monthly":
            next_m = cursor + relativedelta(months=1)
            cursor = _combine_utc(next_m.replace(day=routine.frequency_value), sched_time)

    return results


def _runs_remaining(routine: Routine) -> Optional[int]:
    """Integer count of remaining runs, or None for ongoing."""
    if not routine.end_date or not routine.next_run_at:
        return None
    upcoming = _upcoming_runs(routine, count=500)
    return len(upcoming)


def _total_runs(routine: Routine) -> Optional[int]:
    """Total run count over the full duration, or None for ongoing."""
    if not routine.end_date:
        return None
    from dateutil.relativedelta import relativedelta
    sched_time = routine.schedule_time
    start = _combine_utc(routine.start_date, sched_time)
    count = 0
    cursor = start

    end_utc = _as_utc(routine.end_date)
    while cursor <= end_utc:
        count += 1
        if routine.frequency_type == "every_n_days":
            cursor += timedelta(days=routine.frequency_value)
        elif routine.frequency_type == "weekly":
            cursor += timedelta(weeks=1)
        elif routine.frequency_type == "monthly":
            next_m = cursor + relativedelta(months=1)
            cursor = _combine_utc(next_m.replace(day=routine.frequency_value), sched_time)
        else:
            break
    return count


def _end_date_from_preset(preset: str, start: datetime) -> Optional[datetime]:
    if preset == "2_weeks":
        return start + timedelta(weeks=2)
    if preset == "1_month":
        from dateutil.relativedelta import relativedelta
        return start + relativedelta(months=1)
    return None


def _serialize(routine: Routine) -> RoutineOut:
    return RoutineOut(
        id=routine.id,
        name=routine.name,
        status=routine.status,
        frequency_type=routine.frequency_type,
        frequency_value=routine.frequency_value,
        schedule_time_ist=_utc_time_to_ist_hhmm(routine.schedule_time),
        start_date=routine.start_date,
        end_date=routine.end_date,
        next_run_at=routine.next_run_at,
        runs_remaining=_runs_remaining(routine),
        total_runs=_total_runs(routine),
        items=[
            RoutineItemOut(
                id=item.id,
                item_name=item.item_name,
                quantity=float(item.quantity),
                unit=item.unit,
                swiggy_product_id=item.swiggy_product_id,
                swiggy_product_name=item.swiggy_product_name,
            )
            for item in routine.items
        ],
        upcoming_runs=_upcoming_runs(routine),
    )


# ── Service class ─────────────────────────────────────────────────────────────

class RoutinesService:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get(self, routine_id: str, household_id: str) -> Routine | None:
        result = await self.db.execute(
            select(Routine)
            .options(selectinload(Routine.items))
            .where(Routine.id == routine_id, Routine.household_id == household_id, Routine.status != "deleted")
        )
        return result.scalar_one_or_none()

    async def list_routines(self, household_id: str) -> list[RoutineOut]:
        result = await self.db.execute(
            select(Routine)
            .options(selectinload(Routine.items))
            .where(Routine.household_id == household_id, Routine.status != "deleted")
            .order_by(Routine.created_at.desc())
        )
        return [_serialize(r) for r in result.scalars().all()]

    async def create(self, household_id: str, data: RoutineCreate) -> RoutineOut:
        # Validate monthly day range
        if data.frequency_type == "monthly" and not (1 <= data.frequency_value <= 28):
            raise ValueError("Monthly day must be between 1 and 28")
        if data.frequency_type == "weekly" and not (0 <= data.frequency_value <= 6):
            raise ValueError("Weekly day must be 0 (Mon) to 6 (Sun)")

        sched_utc = _ist_hhmm_to_utc_time(data.schedule_time)
        now_utc = datetime.now(UTC)
        start = now_utc

        # Compute end_date
        end_date: Optional[datetime] = None
        if data.duration_preset and data.duration_preset != "ongoing":
            end_date = _end_date_from_preset(data.duration_preset, start)
        elif data.end_date:
            end_date = data.end_date

        routine = Routine(
            household_id=household_id,
            name=data.name,
            status="active",
            frequency_type=data.frequency_type,
            frequency_value=data.frequency_value,
            schedule_time=sched_utc,
            start_date=start,
            end_date=end_date,
        )
        self.db.add(routine)
        await self.db.flush()  # get routine.id

        for item_in in data.items:
            self.db.add(RoutineItem(
                routine_id=routine.id,
                item_name=item_in.item_name,
                quantity=item_in.quantity,
                unit=item_in.unit,
                swiggy_product_id=item_in.swiggy_product_id,
                swiggy_product_name=item_in.swiggy_product_name,
            ))

        # Compute first next_run_at
        routine.next_run_at = compute_next_run_at(routine, after=now_utc)

        await self.db.commit()
        await self.db.refresh(routine, attribute_names=["items"])
        return _serialize(routine)

    async def get(self, routine_id: str, household_id: str) -> RoutineOut | None:
        r = await self._get(routine_id, household_id)
        return _serialize(r) if r else None

    async def patch(self, routine_id: str, household_id: str, data: RoutinePatch) -> RoutineOut | None:
        r = await self._get(routine_id, household_id)
        if not r:
            return None

        if data.name is not None:
            r.name = data.name
        if data.frequency_type is not None:
            r.frequency_type = data.frequency_type
        if data.frequency_value is not None:
            r.frequency_value = data.frequency_value
        if data.schedule_time is not None:
            r.schedule_time = _ist_hhmm_to_utc_time(data.schedule_time)
        if data.end_date is not None:
            r.end_date = data.end_date
        if data.items is not None:
            # Replace all items
            for existing in list(r.items):
                await self.db.delete(existing)
            await self.db.flush()
            for item_in in data.items:
                self.db.add(RoutineItem(
                    routine_id=r.id,
                    item_name=item_in.item_name,
                    quantity=item_in.quantity,
                    unit=item_in.unit,
                    swiggy_product_id=item_in.swiggy_product_id,
                    swiggy_product_name=item_in.swiggy_product_name,
                ))

        # Recompute next_run_at if schedule changed
        if any(v is not None for v in [data.frequency_type, data.frequency_value, data.schedule_time]):
            r.next_run_at = compute_next_run_at(r)

        await self.db.commit()
        await self.db.refresh(r, attribute_names=["items"])
        return _serialize(r)

    async def delete(self, routine_id: str, household_id: str) -> bool:
        r = await self._get(routine_id, household_id)
        if not r:
            return False
        r.status = "deleted"
        await self.db.commit()
        return True

    async def pause(self, routine_id: str, household_id: str) -> RoutineOut | None:
        r = await self._get(routine_id, household_id)
        if not r or r.status != "active":
            return None
        r.status = "paused"
        r.paused_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(r, attribute_names=["items"])
        return _serialize(r)

    async def resume(self, routine_id: str, household_id: str) -> RoutineOut | None:
        r = await self._get(routine_id, household_id)
        if not r or r.status != "paused":
            return None

        days_paused = 0
        if r.paused_at:
            days_paused = (datetime.now(UTC) - r.paused_at).days
            r.total_days_paused = (r.total_days_paused or 0) + days_paused

        if r.end_date and days_paused > 0:
            r.end_date = r.end_date + timedelta(days=days_paused)

        r.paused_at = None
        r.status = "active"
        r.next_run_at = compute_next_run_at(r)
        await self.db.commit()
        await self.db.refresh(r, attribute_names=["items"])
        return _serialize(r)

    async def skip_next(self, routine_id: str, household_id: str) -> RoutineOut | None:
        r = await self._get(routine_id, household_id)
        if not r or r.status != "active" or not r.next_run_at:
            return None

        # Log the skipped run
        self.db.add(RoutineRun(
            routine_id=r.id,
            scheduled_at=r.next_run_at,
            status="skipped",
            skip_reason="user_skip",
        ))

        # Advance next_run_at
        r.next_run_at = compute_next_run_at(r, after=r.next_run_at)
        if r.end_date and (not r.next_run_at or r.next_run_at > _as_utc(r.end_date)):
            r.status = "ended"

        await self.db.commit()
        await self.db.refresh(r, attribute_names=["items"])
        return _serialize(r)

    async def list_runs(self, routine_id: str, household_id: str, limit: int = 20) -> list[RoutineRunOut]:
        result = await self.db.execute(
            select(RoutineRun)
            .join(Routine, Routine.id == RoutineRun.routine_id)
            .where(RoutineRun.routine_id == routine_id, Routine.household_id == household_id)
            .order_by(RoutineRun.scheduled_at.desc())
            .limit(limit)
        )
        return [
            RoutineRunOut(
                id=run.id,
                scheduled_at=run.scheduled_at,
                status=run.status,
                skip_reason=run.skip_reason,
                placed_at=run.placed_at,
                total_amount=float(run.total_amount) if run.total_amount else None,
                order_id=run.order_id,
            )
            for run in result.scalars().all()
        ]
