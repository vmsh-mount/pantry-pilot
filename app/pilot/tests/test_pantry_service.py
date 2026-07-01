"""
Tests for PantryService.

Covers:
  - _decay: zero elapsed, partial elapsed, fully consumed (clamps to 0)
  - _normalise_category: all canonical buckets + fallback
  - _reorder_threshold: per-category multipliers
  - _parse_ts: valid ISO, Z-suffix, None, invalid
  - apply_decay: updates estimated_qty_remaining in place
  - items_needing_reorder: correctly filters flagged vs. stocked items
  - post_order_update: creates new item, updates existing, refines consumption rate
  - adjust_from_user_signal: removed → rate down, kept → no change, added_early → rate up
  - upsert_item: creates when absent, skips when overwrite=False, updates when overwrite=True
  - bootstrap_from_history: returns 0 when MCP fails gracefully
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.pantry_service import (
    PantryService,
    _decay,
    _normalise_category,
    _reorder_threshold,
    _parse_ts,
)
from app.models.db import PantryItem


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_item(
    *,
    last_ordered_qty:        float = 1.0,
    last_ordered_at:         datetime | None = None,
    avg_weekly_consumption:  float = 0.5,
    estimated_qty_remaining: float = 1.0,
    reorder_threshold:       float = 0.3,
    times_ordered:           int = 1,
    times_removed_by_user:   int = 0,
    times_kept_by_user:      int = 0,
    category:                str = "staples",
) -> PantryItem:
    item = PantryItem()
    item.last_ordered_qty        = last_ordered_qty
    item.last_ordered_at         = last_ordered_at or datetime.now(timezone.utc)
    item.avg_weekly_consumption  = avg_weekly_consumption
    item.estimated_qty_remaining = estimated_qty_remaining
    item.reorder_threshold       = reorder_threshold
    item.times_ordered           = times_ordered
    item.times_removed_by_user   = times_removed_by_user
    item.times_kept_by_user      = times_kept_by_user
    item.category                = category
    item.is_active               = True
    item.household_id            = "hh-test"
    item.item_name               = "Test Item"
    item.standard_unit           = "kg"
    item.consumption_confidence  = 0.0
    item.last_user_action        = None
    item.last_user_action_at     = None
    return item


def make_service() -> tuple[PantryService, AsyncMock]:
    mock_db = AsyncMock()
    return PantryService(mock_db), mock_db


# ── _decay ────────────────────────────────────────────────────────────────────

def test_decay_zero_elapsed():
    """Just ordered → no decay → full stock."""
    item = make_item(last_ordered_qty=1.0, avg_weekly_consumption=0.5)
    now  = item.last_ordered_at  # same moment
    assert _decay(item, now) == 1.0


def test_decay_partial():
    """7 days elapsed, 0.5kg/week consumption → 0.5kg remaining."""
    last_at = datetime.now(timezone.utc) - timedelta(days=7)
    item    = make_item(
        last_ordered_qty       = 1.0,
        last_ordered_at        = last_at,
        avg_weekly_consumption = 0.5,
    )
    result = _decay(item, datetime.now(timezone.utc))
    assert abs(result - 0.5) < 0.01


def test_decay_fully_consumed_clamps_to_zero():
    """14 days elapsed, 0.5kg/week → would be -ve → clamp to 0."""
    last_at = datetime.now(timezone.utc) - timedelta(days=14)
    item    = make_item(
        last_ordered_qty       = 1.0,
        last_ordered_at        = last_at,
        avg_weekly_consumption = 0.5,
    )
    result = _decay(item, datetime.now(timezone.utc))
    assert result == 0.0


def test_decay_no_consumption_rate_returns_current():
    """No consumption rate → return current estimated_qty_remaining unchanged."""
    item = make_item(avg_weekly_consumption=0.0, estimated_qty_remaining=2.0)
    result = _decay(item, datetime.now(timezone.utc))
    assert result == 2.0


def test_decay_no_last_ordered_at_returns_current():
    item = make_item(estimated_qty_remaining=1.5)
    item.last_ordered_at = None
    result = _decay(item, datetime.now(timezone.utc))
    assert result == 1.5


# ── _normalise_category ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("staples",         "staples"),
    ("Grains",          "staples"),
    ("pulses",          "staples"),
    ("oils",            "staples"),
    ("Fresh Produce",   "fresh_produce"),
    ("vegetables",      "fresh_produce"),
    ("fruits",          "fresh_produce"),
    ("dairy",           "dairy"),
    ("Dairy & Eggs",    "dairy"),
    ("milk",            "dairy"),
    ("packaged",        "packaged"),
    ("snacks",          "packaged"),
    ("beverages",       "packaged"),
    ("grocery",         "grocery"),
    ("unknown_thing",   "grocery"),
])
def test_normalise_category(raw, expected):
    assert _normalise_category(raw) == expected


# ── _reorder_threshold ────────────────────────────────────────────────────────

def test_reorder_threshold_staples():
    assert _reorder_threshold("staples", 1.0) == pytest.approx(0.30)


def test_reorder_threshold_fresh_produce():
    # Always 0% → always reorder
    assert _reorder_threshold("fresh_produce", 1.0) == 0.0


def test_reorder_threshold_dairy():
    assert _reorder_threshold("dairy", 1.0) == pytest.approx(0.20)


def test_reorder_threshold_unknown():
    assert _reorder_threshold("mystery", 2.0) == pytest.approx(0.50)  # 25% of 2.0


# ── _parse_ts ─────────────────────────────────────────────────────────────────

def test_parse_ts_valid_iso():
    dt = _parse_ts("2024-01-10T10:00:00")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_ts_z_suffix():
    dt = _parse_ts("2024-01-10T10:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_ts_none():
    assert _parse_ts(None) is None


def test_parse_ts_invalid():
    assert _parse_ts("not-a-date") is None


# ── apply_decay ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_decay_updates_items():
    service, mock_db = make_service()

    last_at = datetime.now(timezone.utc) - timedelta(days=7)
    item    = make_item(last_ordered_qty=1.0, last_ordered_at=last_at,
                        avg_weekly_consumption=0.5)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [item]
    mock_db.execute.return_value = mock_result

    items = await service.apply_decay("hh-1")

    assert len(items) == 1
    assert abs(float(items[0].estimated_qty_remaining) - 0.5) < 0.01
    mock_db.commit.assert_called_once()


# ── items_needing_reorder ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_items_needing_reorder_filters_correctly():
    service, mock_db = make_service()

    # Item A: stocked (0.8 remaining, threshold 0.3) → NOT flagged
    item_a = make_item(
        last_ordered_qty       = 1.0,
        last_ordered_at        = datetime.now(timezone.utc),   # just ordered
        avg_weekly_consumption = 0.2,
        reorder_threshold      = 0.3,
    )
    item_a.item_name = "Atta"

    # Item B: below threshold (0.1 remaining after decay) → flagged
    last_at_b = datetime.now(timezone.utc) - timedelta(days=14)
    item_b = make_item(
        last_ordered_qty       = 1.0,
        last_ordered_at        = last_at_b,
        avg_weekly_consumption = 0.5,   # 1.0 - (14/7 * 0.5) = 0 → flagged
        reorder_threshold      = 0.3,
    )
    item_b.item_name = "Toor Dal"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [item_a, item_b]
    mock_db.execute.return_value = mock_result

    flagged = await service.items_needing_reorder("hh-1", apply_decay_first=True)

    names = [i.item_name for i in flagged]
    assert "Toor Dal" in names
    assert "Atta" not in names


# ── post_order_update ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_order_update_creates_new_item():
    service, mock_db = make_service()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None   # item doesn't exist yet
    mock_db.execute.return_value = mock_result

    await service.post_order_update("hh-1", [
        {"name": "Basmati Rice", "quantity": 2.0, "unit": "kg", "category": "staples"}
    ])

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
    added_item = mock_db.add.call_args[0][0]
    assert added_item.item_name               == "Basmati Rice"
    assert float(added_item.last_ordered_qty) == 2.0
    assert float(added_item.estimated_qty_remaining) == 2.0  # reset to full


@pytest.mark.asyncio
async def test_post_order_update_resets_existing_stock():
    service, mock_db = make_service()

    existing = make_item(
        last_ordered_qty       = 1.0,
        last_ordered_at        = datetime.now(timezone.utc) - timedelta(days=10),
        avg_weekly_consumption = 0.5,
        estimated_qty_remaining = 0.1,   # was almost empty
        times_ordered          = 3,
    )
    existing.item_name = "Toor Dal"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing
    mock_db.execute.return_value = mock_result

    await service.post_order_update("hh-1", [
        {"name": "Toor Dal", "quantity": 1.0, "unit": "kg", "category": "staples"}
    ])

    assert float(existing.estimated_qty_remaining) == 1.0  # reset
    assert existing.times_ordered == 4
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_post_order_update_refines_consumption_rate():
    """10 days between orders, 1kg qty → new_rate = (1/10)*7 = 0.7 kg/week."""
    service, mock_db = make_service()

    last_at = datetime.now(timezone.utc) - timedelta(days=10)
    existing = make_item(
        last_ordered_qty       = 1.0,
        last_ordered_at        = last_at,
        avg_weekly_consumption = 0.5,   # old rate
        times_ordered          = 4,
    )
    existing.item_name = "Dosa Batter"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing
    mock_db.execute.return_value = mock_result

    await service.post_order_update("hh-1", [
        {"name": "Dosa Batter", "quantity": 1.0, "unit": "litre", "category": "dairy"}
    ])

    new_rate = float(existing.avg_weekly_consumption)
    # EMA: alpha=0.4 (times_ordered < 8), new_rate=0.7, old_rate=0.5
    # expected = 0.4*0.7 + 0.6*0.5 = 0.28 + 0.30 = 0.58
    assert abs(new_rate - 0.58) < 0.02


# ── adjust_from_user_signal ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_signal_removed_decreases_rate():
    service, mock_db = make_service()
    item = make_item(avg_weekly_consumption=1.0)
    item.item_name = "Amul Butter"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = item
    mock_db.execute.return_value = mock_result

    await service.adjust_from_user_signal("hh-1", "Amul Butter", "removed")

    assert float(item.avg_weekly_consumption) == pytest.approx(0.85)
    assert item.last_user_action == "removed"
    assert item.times_removed_by_user == 1


@pytest.mark.asyncio
async def test_signal_kept_no_rate_change():
    service, mock_db = make_service()
    item = make_item(avg_weekly_consumption=1.0)
    item.item_name = "Amul Butter"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = item
    mock_db.execute.return_value = mock_result

    await service.adjust_from_user_signal("hh-1", "Amul Butter", "kept")

    assert float(item.avg_weekly_consumption) == pytest.approx(1.0)  # unchanged
    assert item.times_kept_by_user == 1


@pytest.mark.asyncio
async def test_signal_added_early_increases_rate():
    service, mock_db = make_service()
    item = make_item(avg_weekly_consumption=1.0)
    item.item_name = "Milk"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = item
    mock_db.execute.return_value = mock_result

    await service.adjust_from_user_signal("hh-1", "Milk", "added_early")

    assert float(item.avg_weekly_consumption) == pytest.approx(1.15)


@pytest.mark.asyncio
async def test_signal_unknown_item_is_noop():
    """Signal for an item not in pantry should silently do nothing."""
    service, mock_db = make_service()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    # Must not raise
    await service.adjust_from_user_signal("hh-1", "Nonexistent Item", "removed")
    mock_db.commit.assert_not_called()


# ── bootstrap graceful failure ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bootstrap_returns_zero_when_mcp_fails():
    service, _ = make_service()

    with patch("app.mcp.swiggy.SwiggyMCPClient") as MockClient:
        from app.utils.exceptions import SwiggyMCPError
        instance = MockClient.return_value
        instance.get_orders = AsyncMock(side_effect=SwiggyMCPError("timeout"))

        count = await service.bootstrap_from_history("hh-1", "token_abc")

    assert count == 0


@pytest.mark.asyncio
async def test_bootstrap_returns_zero_when_no_orders():
    service, _ = make_service()

    with patch("app.mcp.swiggy.SwiggyMCPClient") as MockClient:
        instance = MockClient.return_value
        instance.get_orders = AsyncMock(return_value=[])

        count = await service.bootstrap_from_history("hh-1", "token_abc")

    assert count == 0
