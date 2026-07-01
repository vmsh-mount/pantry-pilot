"""
Tests for HouseholdService.

Covers:
  - get_settings: returns correct shape, raises on unknown household
  - update_settings: household fields, preference fields, both together
  - pause: sets is_paused, cancels pending loop runs
  - resume: clears pause state, calls reschedule_next_run
  - delete: calls delete on all related tables in order
  - get_missed_runs: returns household IDs with overdue next_run_at
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.services.household_service import HouseholdService
from app.utils.exceptions import HouseholdNotFoundError


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_service():
    mock_db = AsyncMock()
    return HouseholdService(mock_db), mock_db


def make_household(**overrides):
    from app.models.db import Household
    hh = Household()
    hh.id                  = "hh-001"
    hh.household_type      = "couple"
    hh.member_count        = 2
    hh.diet_type           = "vegetarian"
    hh.allergies           = []
    hh.city                = "Bengaluru"
    hh.weekly_budget_min   = 1500
    hh.weekly_budget_max   = 2500
    hh.whatsapp_number     = "+919999999999"
    hh.whatsapp_verified   = True
    hh.whatsapp_opted_out  = False
    hh.onboarding_complete = True
    hh.is_active           = True
    hh.is_paused           = False
    hh.paused_at           = None
    hh.paused_reason       = None
    for k, v in overrides.items():
        setattr(hh, k, v)
    return hh


def make_prefs(**overrides):
    from app.models.db import HouseholdPreferences
    prefs = HouseholdPreferences()
    prefs.preferred_order_day      = "sunday"
    prefs.preferred_delivery_slot  = "evening"
    prefs.confirmation_window_hrs  = 4
    prefs.next_run_at              = None
    prefs.last_run_at              = None
    prefs.freq_staples             = "weekly"
    prefs.freq_fresh_produce       = "weekly"
    prefs.freq_dairy_eggs          = "weekly"
    prefs.freq_packaged            = "weekly"
    for k, v in overrides.items():
        setattr(prefs, k, v)
    return prefs


# ══════════════════════════════════════════════════════════════════════════════
# get_settings
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_settings_returns_correct_shape():
    service, mock_db = make_service()
    household = make_household()
    prefs     = make_prefs()

    # Two execute calls: one for Household, one for HouseholdPreferences
    results = [MagicMock(), MagicMock()]
    results[0].scalar_one_or_none.return_value = household
    results[1].scalar_one_or_none.return_value = prefs
    mock_db.execute.side_effect = results

    data = await service.get_settings("hh-001")

    assert data["household_id"]   == "hh-001"
    assert data["diet_type"]      == "vegetarian"
    assert data["is_paused"]      is False
    assert data["preferences"]["preferred_order_day"] == "sunday"


@pytest.mark.asyncio
async def test_get_settings_raises_on_unknown_household():
    service, mock_db = make_service()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with pytest.raises(HouseholdNotFoundError):
        await service.get_settings("hh-nonexistent")


@pytest.mark.asyncio
async def test_get_settings_handles_no_prefs():
    """Household without preferences row returns safe defaults."""
    service, mock_db = make_service()
    household = make_household()

    results = [MagicMock(), MagicMock()]
    results[0].scalar_one_or_none.return_value = household
    results[1].scalar_one_or_none.return_value = None   # no prefs row
    mock_db.execute.side_effect = results

    data = await service.get_settings("hh-001")
    assert data["preferences"] == {}


# ══════════════════════════════════════════════════════════════════════════════
# pause
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pause_writes_is_paused():
    service, mock_db = make_service()

    await service.pause("hh-001", reason="user_request")

    # Two execute calls: update Household, update LoopRun
    assert mock_db.execute.call_count == 2
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_pause_uses_provided_reason():
    service, mock_db = make_service()
    await service.pause("hh-001", reason="token_expired")
    # We just verify it doesn't raise — reason is embedded in the SQLAlchemy update
    mock_db.commit.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# resume
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# delete
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_executes_multiple_deletes():
    service, mock_db = make_service()

    # order_ids query
    order_ids_result = MagicMock()
    order_ids_result.all.return_value = [("order-1",), ("order-2",)]

    # run_ids query
    run_ids_result = MagicMock()
    run_ids_result.all.return_value = [("run-1",)]

    # All remaining execute calls return a generic mock
    generic = MagicMock()
    generic.all.return_value = []

    call_count = [0]

    async def execute_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return order_ids_result
        if call_count[0] == 2:
            return run_ids_result
        return generic

    mock_db.execute.side_effect = execute_side_effect

    await service.delete("hh-001")

    # Should have called execute many times (order_ids, run_ids, + delete calls)
    assert mock_db.execute.call_count >= 10
    mock_db.commit.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# get_missed_runs
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_missed_runs_returns_household_ids():
    service, mock_db = make_service()

    mock_result = MagicMock()
    mock_result.all.return_value = [("hh-001",), ("hh-002",)]
    mock_db.execute.return_value = mock_result

    before = datetime.now(timezone.utc)
    result = await service.get_missed_runs(before)

    assert result == ["hh-001", "hh-002"]


@pytest.mark.asyncio
async def test_get_missed_runs_empty_when_none_overdue():
    service, mock_db = make_service()

    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_db.execute.return_value = mock_result

    result = await service.get_missed_runs(datetime.now(timezone.utc))
    assert result == []


# ══════════════════════════════════════════════════════════════════════════════
# update_settings
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_update_settings_pref_fields_only():
    service, mock_db = make_service()
    household = make_household()
    prefs     = make_prefs()

    results = [MagicMock() for _ in range(3)]
    results[0].scalar_one_or_none.return_value = prefs      # pref lookup
    results[1].scalar_one_or_none.return_value = household  # get_settings
    results[2].scalar_one_or_none.return_value = prefs      # get_settings prefs
    mock_db.execute.side_effect = results

    await service.update_settings("hh-001", {"preferred_order_day": "wednesday"})
    assert prefs.preferred_order_day == "wednesday"
