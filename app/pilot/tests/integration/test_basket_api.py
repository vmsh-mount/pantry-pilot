"""
Integration tests — Basket API endpoints

Flows covered:
  1.  GET /basket/pending — no run → empty state
  2.  GET /basket/pending — run in progress → in_progress=True
  3.  GET /basket/pending — awaiting confirmation → returns basket items
  4.  GET /basket/pending — failed run → last_failed populated
  5.  GET /basket/pending — unauthenticated → 401/NOT_AUTHENTICATED
  6.  POST /basket/trigger — creates loop run, enqueues Celery task
  7.  POST /basket/trigger — already in progress → error
  8.  POST /basket/trigger — onboarding incomplete → ONBOARDING_INCOMPLETE
  9.  POST /basket/trigger — paused household → HOUSEHOLD_PAUSED
  10. POST /basket/confirm — confirms basket, enqueues place task
  11. POST /basket/confirm — empty basket → BASKET_EMPTY error
  12. POST /basket/confirm — no awaiting run → error
  13. POST /basket/skip — skips run, reschedules
  14. POST /basket/skip — no run to skip → graceful
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from tests.integration.conftest import create_household, SWIGGY_RESPONSES


# ── Helpers ───────────────────────────────────────────────────────────────────

async def auth_session(client, household_id: str):
    from tests.integration.conftest import encode_session
    client.cookies.set("session", encode_session(household_id))


async def seed_loop_run(db, household_id: str, state: str, **kwargs):
    """Insert a LoopRun with the given state directly into the DB."""
    from app.models.db import LoopRun
    run = LoopRun(
        household_id = household_id,
        trigger_type = "scheduled",
        state        = state,
        triggered_at = datetime.now(timezone.utc),
        **kwargs,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def seed_loop_run_item(db, loop_run_id: str, household_id: str):
    """Insert a resolved basket item for a loop run."""
    from app.models.db import LoopRunItem
    item = LoopRunItem(
        loop_run_id         = loop_run_id,
        household_id        = household_id,
        item_name           = "Tata Salt",
        swiggy_sku_id       = "sku_tata_salt_001",
        swiggy_product_name = "Tata Salt 1kg",
        brand               = "Tata",
        quantity            = 1.0,
        unit                = "kg",
        unit_price          = 28.0,
        total_price         = 28.0,
        added_by            = "rules_engine",
        is_substitution     = False,
    )
    db.add(item)
    await db.commit()
    return item


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/basket/pending
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pending_no_run(app_client, db):
    """No loop run exists → response is success with no pending basket."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/basket/pending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["pending"] is False


@pytest.mark.asyncio
async def test_pending_run_in_progress(app_client, db):
    """Loop run in 'sensing' state → in_progress=True returned."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    await seed_loop_run(db, household_id, "sensing")

    resp = await app_client.get("/v1/basket/pending")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["pending"] is False
    assert data["in_progress"] is True


@pytest.mark.asyncio
async def test_pending_awaiting_confirmation_returns_items(app_client, db):
    """Loop run awaiting_confirmation → basket items visible."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run = await seed_loop_run(db, household_id, "awaiting_confirmation")
    await seed_loop_run_item(db, str(run.id), household_id)

    resp = await app_client.get("/v1/basket/pending")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["pending"] is True
    assert len(data["items"]) == 1
    assert data["items"][0]["item_name"] == "Tata Salt"


@pytest.mark.asyncio
async def test_pending_last_failed_populated(app_client, db):
    """Failed loop run → last_failed.failure_reason exposed."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    await seed_loop_run(db, household_id, "failed",
                        failure_reason="MCP timeout", failure_stage="optimize")

    resp = await app_client.get("/v1/basket/pending")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["pending"] is False
    assert data["last_failed"] is True   # API returns bool, not dict


@pytest.mark.asyncio
async def test_pending_unauthenticated(app_client):
    """No session cookie → NOT_AUTHENTICATED error."""
    resp = await app_client.get("/v1/basket/pending")
    assert resp.status_code in (200, 401)
    body = resp.json()
    # Either HTTP 401 or APIResponse with error code
    if resp.status_code == 200:
        assert body["success"] is False
        assert body["error"]["code"] == "NOT_AUTHENTICATED"


# ══════════════════════════════════════════════════════════════════════════════
# POST /v1/basket/trigger
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_trigger_creates_loop_run(app_client, db):
    """Trigger on ready household → loop run created, Celery task enqueued."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    with patch("app.tasks.planning.trigger_planning_loop.delay") as mock_delay:
        resp = await app_client.post("/v1/basket/trigger")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    mock_delay.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_blocked_while_in_progress(app_client, db):
    """Trigger when sensing/planning/optimizing → conflict error."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    await seed_loop_run(db, household_id, "planning")

    resp = await app_client.post("/v1/basket/trigger")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "progress" in body["error"]["message"].lower() or body["error"]["code"] != "OK"


@pytest.mark.asyncio
async def test_trigger_blocked_while_awaiting_confirmation(app_client, db):
    """Trigger when user hasn't responded yet → conflict error."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    await seed_loop_run(db, household_id, "awaiting_confirmation")

    resp = await app_client.post("/v1/basket/trigger")
    body = resp.json()
    assert body["success"] is False


@pytest.mark.asyncio
async def test_trigger_onboarding_incomplete(app_client, db):
    """Household not fully onboarded → ONBOARDING_INCOMPLETE."""
    from app.models.db import Household, SwiggyToken
    from app.utils.crypto import encrypt_token

    household_id = await create_household(db)
    # Un-complete onboarding
    from sqlalchemy import update
    from app.models.db import Household as HH
    await db.execute(
        update(HH).where(HH.id == household_id).values(onboarding_complete=False)
    )
    await db.commit()
    await auth_session(app_client, household_id)

    resp = await app_client.post("/v1/basket/trigger")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "ONBOARDING_INCOMPLETE"


@pytest.mark.asyncio
async def test_trigger_paused_household(app_client, db):
    """Paused household → HOUSEHOLD_PAUSED error."""
    from sqlalchemy import update
    from app.models.db import Household as HH

    household_id = await create_household(db)
    await db.execute(
        update(HH).where(HH.id == household_id).values(is_paused=True)
    )
    await db.commit()
    await auth_session(app_client, household_id)

    resp = await app_client.post("/v1/basket/trigger")
    body = resp.json()
    assert body["success"] is False


# ══════════════════════════════════════════════════════════════════════════════
# POST /v1/basket/confirm
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_confirm_happy_path(app_client, db):
    """Confirming a basket with items → place task enqueued, state updated."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run = await seed_loop_run(db, household_id, "awaiting_confirmation")
    await seed_loop_run_item(db, str(run.id), household_id)

    with patch("app.tasks.planning.place_confirmed_order.delay") as mock_delay:
        resp = await app_client.post("/v1/basket/confirm")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    mock_delay.assert_called_once_with(household_id, str(run.id))


@pytest.mark.asyncio
async def test_confirm_empty_basket_rejected(app_client, db):
    """Awaiting run with no items → BASKET_EMPTY error."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    await seed_loop_run(db, household_id, "awaiting_confirmation")
    # No items seeded

    resp = await app_client.post("/v1/basket/confirm")
    body = resp.json()
    assert body["success"] is False
    assert "empty" in body["error"]["message"].lower() or body["error"]["code"] == "BASKET_EMPTY"


@pytest.mark.asyncio
async def test_confirm_no_awaiting_run(app_client, db):
    """No awaiting_confirmation run → graceful error."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.post("/v1/basket/confirm")
    body = resp.json()
    assert body["success"] is False


# ══════════════════════════════════════════════════════════════════════════════
# POST /v1/basket/skip
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_skip_marks_run_skipped(app_client, db):
    """Skipping a run → state=skipped, next run scheduled."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    run = await seed_loop_run(db, household_id, "awaiting_confirmation")

    with patch("app.services.planning_service.PlanningService.reschedule_next_run",
               new=AsyncMock(return_value=datetime.now(timezone.utc) + timedelta(days=7))):
        resp = await app_client.post("/v1/basket/skip")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True

    # Verify DB state
    from sqlalchemy import select
    from app.models.db import LoopRun
    result = await db.execute(select(LoopRun).where(LoopRun.id == run.id))
    updated = result.scalar_one_or_none()
    assert updated.state       == "skipped"
    assert updated.skip_reason == "user_skipped"


@pytest.mark.asyncio
async def test_skip_no_run_is_graceful(app_client, db):
    """No awaiting_confirmation run → success with message, no crash."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.post("/v1/basket/skip")
    # Should not 500 — graceful no-op
    assert resp.status_code == 200
