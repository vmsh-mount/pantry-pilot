"""
Unit tests — app/tasks/routines.py (_execute coroutine)

Each test patches the DB, MCP client, and Redis to isolate a single
failure/success path of the execute_routine_run task.

NOTE: _execute uses local imports inside the coroutine, so all patches
must target the source module (e.g. app.database.AsyncSessionLocal),
not app.tasks.routines.*.

Flows covered:
  1.  Routine not found → returns early, no DB writes
  2.  Routine status != active → returns early
  3.  Household paused → RoutineRun(status=skipped, reason=household_paused)
  4.  Redis lock timeout → RoutineRun(status=failed, reason=lock_timeout)
  5.  Token expired (AuthService raises) → RoutineRun(status=failed, reason=token_expired)
  6.  Token is None → RoutineRun(status=failed, reason=token_expired)
  7.  All items unavailable (no product_id, search empty) →
        RoutineRun(status=failed, reason=all_items_unavailable)
  8.  Checkout fails → RoutineRun(status=failed, reason=checkout_failed)
  9.  Happy path (all items have product_id) →
        RoutineRun(status=placed), Order created, next_run_at advanced
  10. Partial path (one item found, one not) →
        RoutineRun(status=partial), skipped_items not empty
  11. _check_due: routines past window logged as missed, next_run_at advanced
  12. _check_due: due routines enqueued via apply_async
"""

import json
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

UTC = timezone.utc

# Canonical patch targets (all are local imports inside the coroutine)
_DB       = "app.database.AsyncSessionLocal"
_COMPUTE  = "app.services.routines_service.compute_next_run_at"
_REDIS    = "app.redis.get_redis"
_AUTH     = "app.services.auth_service.AuthService"
_MCP      = "app.mcp.swiggy.SwiggyMCPClient"
_NOTIFY   = "app.tasks.routines._notify_wa"


# ── Factories ─────────────────────────────────────────────────────────────────

def _make_item(name="Tata Salt", product_id="sku_001", quantity=2):
    item = MagicMock()
    item.item_name = name
    item.swiggy_product_id = product_id
    item.swiggy_product_name = "Tata Salt 1kg" if product_id else None
    item.quantity = quantity
    return item


def _make_household(is_paused=False, whatsapp_number="+910000000000"):
    h = MagicMock()
    h.is_paused = is_paused
    h.whatsapp_number = whatsapp_number
    return h


def _make_routine(
    status="active",
    items=None,
    next_run_at=None,
    end_date=None,
    household_id="hh-001",
):
    r = MagicMock()
    r.id = "routine-001"
    r.household_id = household_id
    r.status = status
    r.name = "Weekly Staples"
    r.items = items if items is not None else [_make_item()]
    r.next_run_at = next_run_at or datetime.now(UTC) + timedelta(minutes=5)
    r.end_date = end_date
    r.household = _make_household()
    r.frequency_type = "every_n_days"
    r.frequency_value = 7
    r.schedule_time = MagicMock(hour=2, minute=30)
    r.start_date = datetime(2026, 1, 1, tzinfo=UTC)
    return r


def _make_checkout_result(grand_total=520.0, order_id="swiggy_order_001"):
    res = MagicMock()
    res.grand_total = grand_total
    res.order_id = order_id
    return res


def _make_db(routine=None):
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = routine
    db.execute = AsyncMock(return_value=exec_result)
    db.scalar = AsyncMock(return_value=None)   # prefs → None (no address resolution)
    db.get = AsyncMock(return_value=None)       # address → None
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _redis_with_lock(acquired=True):
    redis = AsyncMock()
    lock = AsyncMock()
    lock.acquire = AsyncMock(return_value=acquired)
    lock.release = AsyncMock()
    redis.lock = MagicMock(return_value=lock)
    return redis, lock


def _mcp_client(checkout_result=None, search_products=None):
    mcp = AsyncMock()
    mcp.clear_cart = AsyncMock()
    mcp.update_cart = AsyncMock(return_value={"grand_total": 520.0})
    mcp.checkout = AsyncMock(return_value=checkout_result or _make_checkout_result())
    sr = MagicMock()
    sr.products = search_products or []
    mcp.search_products = AsyncMock(return_value=sr)
    return mcp


class _FakeSession:
    def __init__(self, db):
        self._db = db
    async def __aenter__(self):
        return self._db
    async def __aexit__(self, *args):
        pass


_NEXT_RUN = datetime(2026, 7, 16, 2, 30, tzinfo=UTC)


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_routine_not_found_exits_early():
    from app.tasks.routines import _execute
    db = _make_db(routine=None)

    with patch(_DB, return_value=_FakeSession(db)):
        await _execute("routine-999")

    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.anyio
async def test_routine_inactive_exits_early():
    from app.tasks.routines import _execute
    routine = _make_routine(status="paused")
    db = _make_db(routine=routine)

    with patch(_DB, return_value=_FakeSession(db)):
        await _execute(routine.id)

    db.add.assert_not_called()


@pytest.mark.anyio
async def test_household_paused_creates_skipped_run():
    from app.tasks.routines import _execute
    routine = _make_routine()
    routine.household.is_paused = True
    db = _make_db(routine=routine)

    with (
        patch(_DB, return_value=_FakeSession(db)),
        patch(_COMPUTE, return_value=_NEXT_RUN),
    ):
        await _execute(routine.id)

    added = db.add.call_args[0][0]
    assert added.status == "skipped"
    assert added.skip_reason == "household_paused"


@pytest.mark.anyio
async def test_lock_timeout_creates_failed_run():
    from app.tasks.routines import _execute
    routine = _make_routine()
    db = _make_db(routine=routine)
    redis, _ = _redis_with_lock(acquired=False)

    with (
        patch(_DB, return_value=_FakeSession(db)),
        patch(_COMPUTE, return_value=_NEXT_RUN),
        patch(_REDIS, return_value=redis),
    ):
        await _execute(routine.id)

    added = db.add.call_args[0][0]
    assert added.status == "failed"
    assert added.skip_reason == "lock_timeout"


@pytest.mark.anyio
async def test_token_expired_from_exception_creates_failed_run():
    from app.tasks.routines import _execute
    routine = _make_routine()
    db = _make_db(routine=routine)
    redis, _ = _redis_with_lock(acquired=True)

    mock_auth = MagicMock()
    mock_auth.return_value.get_valid_token = AsyncMock(side_effect=Exception("session gone"))

    with (
        patch(_DB, return_value=_FakeSession(db)),
        patch(_COMPUTE, return_value=_NEXT_RUN),
        patch(_REDIS, return_value=redis),
        patch(_AUTH, mock_auth),
    ):
        await _execute(routine.id)

    added = db.add.call_args[0][0]
    assert added.status == "failed"
    assert added.skip_reason == "token_expired"


@pytest.mark.anyio
async def test_token_none_creates_failed_run():
    from app.tasks.routines import _execute
    routine = _make_routine()
    db = _make_db(routine=routine)
    redis, _ = _redis_with_lock(acquired=True)

    mock_auth = MagicMock()
    mock_auth.return_value.get_valid_token = AsyncMock(return_value=None)

    with (
        patch(_DB, return_value=_FakeSession(db)),
        patch(_COMPUTE, return_value=_NEXT_RUN),
        patch(_REDIS, return_value=redis),
        patch(_AUTH, mock_auth),
    ):
        await _execute(routine.id)

    added = db.add.call_args[0][0]
    assert added.status == "failed"
    assert added.skip_reason == "token_expired"


@pytest.mark.anyio
async def test_all_items_unavailable_creates_failed_run():
    from app.tasks.routines import _execute

    item = _make_item(name="Mystery Herb", product_id=None)
    routine = _make_routine(items=[item])
    db = _make_db(routine=routine)
    redis, _ = _redis_with_lock(acquired=True)

    mock_auth = MagicMock()
    mock_auth.return_value.get_valid_token = AsyncMock(return_value="token-abc")
    mcp = _mcp_client(search_products=[])

    with (
        patch(_DB, return_value=_FakeSession(db)),
        patch(_COMPUTE, return_value=_NEXT_RUN),
        patch(_REDIS, return_value=redis),
        patch(_AUTH, mock_auth),
        patch(_MCP, return_value=mcp),
        patch(_NOTIFY, new=AsyncMock()),
    ):
        await _execute(routine.id)

    calls = [c[0][0] for c in db.add.call_args_list]
    run_records = [c for c in calls if hasattr(c, "status")]
    assert any(r.status == "failed" and r.skip_reason == "all_items_unavailable" for r in run_records)


@pytest.mark.anyio
async def test_checkout_failure_creates_failed_run():
    from app.tasks.routines import _execute

    routine = _make_routine(items=[_make_item(product_id="sku_001")])
    db = _make_db(routine=routine)
    redis, _ = _redis_with_lock(acquired=True)

    mock_auth = MagicMock()
    mock_auth.return_value.get_valid_token = AsyncMock(return_value="token-abc")

    mcp = _mcp_client()
    mcp.checkout = AsyncMock(side_effect=Exception("payment failed"))

    with (
        patch(_DB, return_value=_FakeSession(db)),
        patch(_COMPUTE, return_value=_NEXT_RUN),
        patch(_REDIS, return_value=redis),
        patch(_AUTH, mock_auth),
        patch(_MCP, return_value=mcp),
    ):
        await _execute(routine.id)

    calls = [c[0][0] for c in db.add.call_args_list]
    run_records = [c for c in calls if hasattr(c, "status")]
    assert any(r.status == "failed" and r.skip_reason == "checkout_failed" for r in run_records)


@pytest.mark.anyio
async def test_happy_path_places_order_and_advances_next_run():
    from app.tasks.routines import _execute

    routine = _make_routine(items=[_make_item(product_id="sku_001")])
    db = _make_db(routine=routine)
    redis, _ = _redis_with_lock(acquired=True)

    mock_auth = MagicMock()
    mock_auth.return_value.get_valid_token = AsyncMock(return_value="token-abc")
    mcp = _mcp_client(checkout_result=_make_checkout_result(grand_total=520.0))

    with (
        patch(_DB, return_value=_FakeSession(db)),
        patch(_COMPUTE, return_value=_NEXT_RUN),
        patch(_REDIS, return_value=redis),
        patch(_AUTH, mock_auth),
        patch(_MCP, return_value=mcp),
        patch(_NOTIFY, new=AsyncMock()),
    ):
        await _execute(routine.id)

    mcp.update_cart.assert_called_once()
    mcp.checkout.assert_called_once()
    assert routine.next_run_at == _NEXT_RUN

    calls = [c[0][0] for c in db.add.call_args_list]
    run_records = [c for c in calls if hasattr(c, "status")]
    assert any(r.status == "placed" for r in run_records)


@pytest.mark.anyio
async def test_partial_path_when_one_item_unavailable():
    from app.tasks.routines import _execute

    item_ok = _make_item(name="Tata Salt", product_id="sku_001")
    item_bad = _make_item(name="Ghost Item", product_id=None)
    routine = _make_routine(items=[item_ok, item_bad])
    db = _make_db(routine=routine)
    redis, _ = _redis_with_lock(acquired=True)

    mock_auth = MagicMock()
    mock_auth.return_value.get_valid_token = AsyncMock(return_value="token-abc")
    mcp = _mcp_client(search_products=[])  # search finds nothing for ghost item

    with (
        patch(_DB, return_value=_FakeSession(db)),
        patch(_COMPUTE, return_value=_NEXT_RUN),
        patch(_REDIS, return_value=redis),
        patch(_AUTH, mock_auth),
        patch(_MCP, return_value=mcp),
        patch(_NOTIFY, new=AsyncMock()),
    ):
        await _execute(routine.id)

    calls = [c[0][0] for c in db.add.call_args_list]
    run_records = [c for c in calls if hasattr(c, "status")]
    partial = [r for r in run_records if r.status == "partial"]
    assert len(partial) == 1
    skipped = json.loads(partial[0].skipped_items)
    assert any(s["item_name"] == "Ghost Item" for s in skipped)


# ── check_due_routines ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_check_due_logs_missed_runs():
    from app.tasks.routines import _check_due

    now = datetime.now(UTC)
    missed = _make_routine()
    missed.next_run_at = now - timedelta(minutes=20)

    db = AsyncMock()
    missed_result = MagicMock()
    missed_result.scalars.return_value.all.return_value = [missed]
    due_result = MagicMock()
    due_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(side_effect=[missed_result, due_result])
    db.add = MagicMock()
    db.commit = AsyncMock()

    with (
        patch(_DB, return_value=_FakeSession(db)),
        patch(_COMPUTE, return_value=now + timedelta(days=7)),
    ):
        await _check_due()

    added = db.add.call_args[0][0]
    assert added.status == "skipped"
    assert added.skip_reason == "missed"


@pytest.mark.anyio
async def test_check_due_enqueues_due_routines():
    from app.tasks.routines import _check_due

    now = datetime.now(UTC)
    due = _make_routine()
    due.next_run_at = now + timedelta(minutes=5)

    db = AsyncMock()
    missed_result = MagicMock()
    missed_result.scalars.return_value.all.return_value = []
    due_result = MagicMock()
    due_result.scalars.return_value.all.return_value = [due]
    db.execute = AsyncMock(side_effect=[missed_result, due_result])
    db.add = MagicMock()
    db.commit = AsyncMock()

    mock_task = MagicMock()
    mock_task.apply_async = MagicMock()

    with (
        patch(_DB, return_value=_FakeSession(db)),
        patch(_COMPUTE, return_value=now + timedelta(days=7)),
        patch("app.tasks.routines.execute_routine_run", mock_task),
    ):
        await _check_due()

    mock_task.apply_async.assert_called_once()
    _, kwargs = mock_task.apply_async.call_args
    assert kwargs["args"] == [due.id]
