"""
Unit tests — routines_service.py

Covers:
  _ist_hhmm_to_utc_time
    1. Morning IST → correct UTC time
    2. Midnight IST → previous-day UTC (18:30)
    3. IST boundary: 00:30 → 19:00 UTC day-before
    4. Round-trip: IST → UTC → back to IST

  compute_next_run_at
    5.  every_n_days: next occurrence is tomorrow when today's slot passed
    6.  every_n_days: correct grid snapping from start_date
    7.  weekly: correct weekday in future
    8.  weekly: same weekday but slot not yet passed → today
    9.  weekly: same weekday and slot passed → next week
    10. monthly: target day in current month (not yet passed)
    11. monthly: target day already passed → next month
    12. Returns None when end_date already passed
    13. Returns None when next candidate exceeds end_date

  _upcoming_runs
    14. Generates exactly count items for every_n_days
    15. Stops at end_date
    16. Weekly: exactly 4 weekly timestamps 7 days apart
    17. Monthly: months increment correctly

  _runs_remaining / _total_runs
    18. Returns None for ongoing (no end_date)
    19. Returns positive integer when end_date set
"""

import pytest
from datetime import datetime, timedelta, timezone, time as dt_time
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def _routine(
    frequency_type="every_n_days",
    frequency_value=7,
    schedule_time=None,
    start_date=None,
    end_date=None,
    next_run_at=None,
):
    """Factory for a lightweight Routine-like object."""
    r = MagicMock()
    r.frequency_type = frequency_type
    r.frequency_value = frequency_value
    r.schedule_time = schedule_time or dt_time(2, 30, tzinfo=UTC)  # 08:00 IST
    r.start_date = start_date or datetime(2026, 1, 1, tzinfo=UTC)
    r.end_date = end_date
    r.next_run_at = next_run_at
    return r


# ── _ist_hhmm_to_utc_time ─────────────────────────────────────────────────────

def test_ist_morning_to_utc():
    from app.services.routines_service import _ist_hhmm_to_utc_time
    t = _ist_hhmm_to_utc_time("08:00")
    assert t.hour == 2 and t.minute == 30


def test_ist_midnight_to_utc():
    from app.services.routines_service import _ist_hhmm_to_utc_time
    t = _ist_hhmm_to_utc_time("00:00")
    # 00:00 IST = 18:30 UTC previous day (stored as 18:30 wrapping)
    assert t.hour == 18 and t.minute == 30


def test_ist_half_hour_boundary():
    from app.services.routines_service import _ist_hhmm_to_utc_time
    t = _ist_hhmm_to_utc_time("00:30")
    assert t.hour == 19 and t.minute == 0


def test_ist_utc_round_trip():
    from app.services.routines_service import _ist_hhmm_to_utc_time, _utc_time_to_ist_hhmm
    inputs = ["08:00", "12:30", "20:00", "00:00", "05:30"]
    for hhmm in inputs:
        utc_t = _ist_hhmm_to_utc_time(hhmm)
        back = _utc_time_to_ist_hhmm(utc_t)
        assert back == hhmm, f"round-trip failed for {hhmm}: got {back}"


# ── compute_next_run_at ───────────────────────────────────────────────────────

def test_every_n_days_slot_passed_today():
    """When today's slot has already passed, next run is tomorrow."""
    from app.services.routines_service import compute_next_run_at
    now = datetime(2026, 7, 9, 10, 0, tzinfo=UTC)  # 10:00 UTC today
    sched = dt_time(2, 30, tzinfo=UTC)              # 02:30 UTC = 08:00 IST (already passed)
    r = _routine(frequency_type="every_n_days", frequency_value=1, schedule_time=sched,
                 start_date=datetime(2026, 7, 1, tzinfo=UTC))
    result = compute_next_run_at(r, after=now)
    assert result is not None
    assert result > now
    assert result.hour == 2 and result.minute == 30


def test_every_n_days_grid_snap():
    """Snaps to the correct grid from start_date, not just +N from now."""
    from app.services.routines_service import compute_next_run_at
    # start_date = Jan 1, frequency = 3 days. Today is Jan 7 (index 6 from start).
    # Grid: Jan 1, 4, 7, 10 ... Jan 7 is exactly on grid.
    sched = dt_time(8, 0, tzinfo=UTC)
    now = datetime(2026, 1, 7, 9, 0, tzinfo=UTC)  # 09:00 UTC — past today's 08:00 slot
    r = _routine(frequency_type="every_n_days", frequency_value=3, schedule_time=sched,
                 start_date=datetime(2026, 1, 1, tzinfo=UTC))
    result = compute_next_run_at(r, after=now)
    assert result is not None
    assert result.day == 10  # next grid point


def test_weekly_future_weekday():
    """Next run lands on the correct target weekday."""
    from app.services.routines_service import compute_next_run_at
    # Wed 2026-07-08 10:00 UTC; target = Sunday (6)
    now = datetime(2026, 7, 8, 10, 0, tzinfo=UTC)
    r = _routine(frequency_type="weekly", frequency_value=6,  # Sunday
                 schedule_time=dt_time(2, 30, tzinfo=UTC))
    result = compute_next_run_at(r, after=now)
    assert result is not None
    assert result.weekday() == 6  # Sunday


def test_weekly_same_day_not_yet_passed():
    """If it's the right weekday and the slot hasn't passed, fire today."""
    from app.services.routines_service import compute_next_run_at
    # Sunday 2026-07-12 01:00 UTC; slot = 02:30 UTC (not yet passed)
    now = datetime(2026, 7, 12, 1, 0, tzinfo=UTC)
    assert now.weekday() == 6  # Sunday
    r = _routine(frequency_type="weekly", frequency_value=6,
                 schedule_time=dt_time(2, 30, tzinfo=UTC))
    result = compute_next_run_at(r, after=now)
    assert result is not None
    assert result.date() == now.date()  # same day


def test_weekly_same_day_slot_passed():
    """If it's the right weekday but slot passed, next run is 7 days later."""
    from app.services.routines_service import compute_next_run_at
    # Sunday 2026-07-12 10:00 UTC; slot = 02:30 UTC (already passed)
    now = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)
    assert now.weekday() == 6  # Sunday
    r = _routine(frequency_type="weekly", frequency_value=6,
                 schedule_time=dt_time(2, 30, tzinfo=UTC))
    result = compute_next_run_at(r, after=now)
    assert result is not None
    assert (result - now).days >= 6


def test_monthly_target_day_not_passed():
    """Target day is later this month."""
    from app.services.routines_service import compute_next_run_at
    now = datetime(2026, 7, 5, 10, 0, tzinfo=UTC)
    r = _routine(frequency_type="monthly", frequency_value=15,
                 schedule_time=dt_time(2, 30, tzinfo=UTC))
    result = compute_next_run_at(r, after=now)
    assert result is not None
    assert result.month == 7 and result.day == 15


def test_monthly_target_day_already_passed():
    """Target day already passed this month → next month."""
    from app.services.routines_service import compute_next_run_at
    now = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
    r = _routine(frequency_type="monthly", frequency_value=15,
                 schedule_time=dt_time(2, 30, tzinfo=UTC))
    result = compute_next_run_at(r, after=now)
    assert result is not None
    assert result.month == 8 and result.day == 15


def test_returns_none_end_date_past():
    """Returns None when end_date is already in the past."""
    from app.services.routines_service import compute_next_run_at
    now = datetime(2026, 7, 9, 10, 0, tzinfo=UTC)
    r = _routine(end_date=datetime(2026, 7, 1, tzinfo=UTC))
    result = compute_next_run_at(r, after=now)
    assert result is None


def test_returns_none_candidate_exceeds_end_date():
    """Returns None when the next candidate falls after end_date."""
    from app.services.routines_service import compute_next_run_at
    now = datetime(2026, 7, 9, 10, 0, tzinfo=UTC)
    r = _routine(
        frequency_type="every_n_days",
        frequency_value=30,
        schedule_time=dt_time(2, 30, tzinfo=UTC),
        start_date=datetime(2026, 7, 1, tzinfo=UTC),
        end_date=datetime(2026, 7, 15, tzinfo=UTC),
    )
    result = compute_next_run_at(r, after=now)
    assert result is None


# ── _upcoming_runs ────────────────────────────────────────────────────────────

def test_upcoming_runs_every_n_days_count():
    from app.services.routines_service import _upcoming_runs
    base = datetime(2026, 7, 10, 2, 30, tzinfo=UTC)
    r = _routine(frequency_type="every_n_days", frequency_value=3,
                 schedule_time=dt_time(2, 30, tzinfo=UTC), next_run_at=base)
    runs = _upcoming_runs(r, count=5)
    assert len(runs) == 5
    for i, run in enumerate(runs):
        assert run == base + timedelta(days=3 * i)


def test_upcoming_runs_stops_at_end_date():
    from app.services.routines_service import _upcoming_runs
    base = datetime(2026, 7, 10, 2, 30, tzinfo=UTC)
    end = datetime(2026, 7, 18, tzinfo=UTC)
    r = _routine(frequency_type="every_n_days", frequency_value=3,
                 schedule_time=dt_time(2, 30, tzinfo=UTC),
                 next_run_at=base, end_date=end)
    runs = _upcoming_runs(r, count=10)
    # Jul 10, 13, 16 are before end; Jul 19 > end
    assert len(runs) == 3


def test_upcoming_runs_weekly_spacing():
    from app.services.routines_service import _upcoming_runs
    base = datetime(2026, 7, 12, 2, 30, tzinfo=UTC)  # Sunday
    r = _routine(frequency_type="weekly", frequency_value=6,
                 schedule_time=dt_time(2, 30, tzinfo=UTC), next_run_at=base)
    runs = _upcoming_runs(r, count=4)
    assert len(runs) == 4
    for i in range(1, 4):
        assert (runs[i] - runs[i - 1]).days == 7


def test_upcoming_runs_monthly_increment():
    from app.services.routines_service import _upcoming_runs
    base = datetime(2026, 7, 15, 2, 30, tzinfo=UTC)
    r = _routine(frequency_type="monthly", frequency_value=15,
                 schedule_time=dt_time(2, 30, tzinfo=UTC), next_run_at=base)
    runs = _upcoming_runs(r, count=3)
    assert runs[0].month == 7
    assert runs[1].month == 8
    assert runs[2].month == 9
    for run in runs:
        assert run.day == 15


# ── _runs_remaining / _total_runs ─────────────────────────────────────────────

def test_runs_remaining_none_for_ongoing():
    from app.services.routines_service import _runs_remaining
    r = _routine(end_date=None, next_run_at=datetime(2026, 7, 10, 2, 30, tzinfo=UTC))
    assert _runs_remaining(r) is None


def test_runs_remaining_positive_with_end_date():
    from app.services.routines_service import _runs_remaining
    base = datetime(2026, 7, 10, 2, 30, tzinfo=UTC)
    end = datetime(2026, 7, 25, tzinfo=UTC)
    r = _routine(frequency_type="every_n_days", frequency_value=7,
                 schedule_time=dt_time(2, 30, tzinfo=UTC),
                 next_run_at=base, end_date=end)
    remaining = _runs_remaining(r)
    assert remaining is not None and remaining > 0
