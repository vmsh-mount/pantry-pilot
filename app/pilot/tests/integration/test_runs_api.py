"""
Integration tests — Runs API endpoints

Endpoints under test:
  GET  /v1/runs                    (list runs with stats + next_run_at)
  GET  /v1/runs/{run_id}/items     (item detail for a single run)

Flows covered:
  GET /v1/runs
    1.  Unauthenticated → NOT_AUTHENTICATED
    2.  No runs yet → empty list, stats all null/zero
    3.  Single completed run → in list, state always present
    4.  Multiple runs — ordered by triggered_at DESC
    5.  Status filter in_progress maps to all 6 active DB states
    6.  Status filter completed returns only completed runs
    7.  Status filter failed returns only failed runs
    8.  Status filter skipped returns only skipped runs
    9.  Status filter awaiting_confirmation returns only that state
    10. Unknown status filter → empty list (not 500)
    11. filtered_count reflects current filter, not total
    12. Without filter, filtered_count == stats.total_runs (single count reuse)
    13. stats.last_order_total comes from completed run's Order.grand_total (not failed)
    14. stats.avg_order_total is SQL AVG over completed runs only
    15. next_run_at populated from HouseholdPreferences
    16. next_run_at null when prefs not set
    17. Pagination: offset works, load more returns remaining
    18. In-progress run exposes raw state (sensing/planning/optimizing) not 'in_progress'
    19. total_price uses Order.grand_total for completed runs
    20. total_price uses LoopRunItem sum for awaiting_confirmation runs (no Order yet)
    21. item_count reflects LoopRunItem count per run

  GET /v1/runs/{run_id}/items
    22. Unauthenticated → NOT_AUTHENTICATED
    23. Run not found → NOT_FOUND (404 behaviour via error code)
    24. Run belongs to another household → NOT_FOUND (not 403)
    25. Happy path — items returned with brand, is_substitution, original_item_name
    26. Substituted item has is_substitution=True and original_item_name set
    27. Empty run (no items) → empty list, not error
    28. added_by values preserved (rules_engine / llm / user_added)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from tests.integration.conftest import create_household, _mcp_ok


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

async def auth_session(client, household_id: str):
    from tests.integration.conftest import encode_session
    client.cookies.set("session", encode_session(household_id))


async def seed_run(db, household_id, state="completed", triggered_offset_days=0):
    from app.models.db import LoopRun
    triggered_at = datetime.now(timezone.utc) - timedelta(days=triggered_offset_days)
    run = LoopRun(
        household_id = household_id,
        trigger_type = "scheduled",
        state        = state,
        triggered_at = triggered_at,
        place_completed_at = triggered_at if state == "completed" else None,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def seed_item(db, loop_run_id, household_id, name="Tata Salt", price=28.0,
                    added_by="rules_engine", is_substitution=False, original_item_name=None):
    from app.models.db import LoopRunItem
    item = LoopRunItem(
        loop_run_id         = loop_run_id,
        household_id        = household_id,
        item_name           = name,
        swiggy_sku_id       = f"sku_{name.lower().replace(' ', '_')}",
        swiggy_product_name = f"{name} 1kg",
        brand               = "Test Brand",
        quantity            = 1.0,
        unit                = "kg",
        unit_price          = price,
        total_price         = price,
        added_by            = added_by,
        is_substitution     = is_substitution,
        original_item_name  = original_item_name,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def seed_order(db, household_id, loop_run, grand_total=520.0):
    """Attach a completed Order to a LoopRun, as the place node would."""
    from app.models.db import Order
    from sqlalchemy import update
    order = Order(
        household_id      = household_id,
        loop_run_id       = loop_run.id,
        swiggy_order_id   = f"swiggy_ord_{loop_run.id[:8]}",
        swiggy_address_id = "addr_home_001",
        item_total        = grand_total * 0.9,
        delivery_fee      = grand_total * 0.05,
        taxes             = grand_total * 0.05,
        grand_total       = grand_total,
        status            = "placed",
    )
    db.add(order)
    await db.flush()

    # Link the order back to the run
    from app.models.db import LoopRun
    from sqlalchemy import update as sa_update
    await db.execute(
        sa_update(LoopRun).where(LoopRun.id == loop_run.id).values(order_id=order.id)
    )
    await db.commit()
    return order


async def set_next_run_at(db, household_id, next_run_at):
    from app.models.db import HouseholdPreferences
    from sqlalchemy import update
    await db.execute(
        update(HouseholdPreferences)
        .where(HouseholdPreferences.household_id == household_id)
        .values(next_run_at=next_run_at)
    )
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs — authentication
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_runs_unauthenticated(app_client):
    """No session → NOT_AUTHENTICATED."""
    resp = await app_client.get("/v1/runs")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_AUTHENTICATED"


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs — empty state
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_runs_empty(app_client, db):
    """No runs yet → empty list, stats all null, filtered_count=0."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    d = data["data"]
    assert d["runs"]           == []
    assert d["filtered_count"] == 0
    assert d["stats"]["total_runs"]       == 0
    assert d["stats"]["last_order_total"] is None
    assert d["stats"]["avg_order_total"]  is None


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs — state always present
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_runs_state_always_present(app_client, db):
    """Every run object must include the raw `state` field."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    await seed_run(db, household_id, state="completed")

    resp = await app_client.get("/v1/runs")
    runs = resp.json()["data"]["runs"]
    assert len(runs) == 1
    assert "state" in runs[0]
    assert runs[0]["state"] == "completed"


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs — ordering
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_runs_ordered_newest_first(app_client, db):
    """Runs returned newest triggered_at first."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    old = await seed_run(db, household_id, state="completed",  triggered_offset_days=7)
    new = await seed_run(db, household_id, state="skipped",    triggered_offset_days=0)

    resp = await app_client.get("/v1/runs")
    runs = resp.json()["data"]["runs"]
    assert runs[0]["id"] == new.id
    assert runs[1]["id"] == old.id


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs — status filter
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_status_filter_in_progress_covers_all_active_states(app_client, db):
    """status=in_progress must match pending/sensing/planning/optimizing/confirmed/placing."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    active_states = ["pending", "sensing", "planning", "optimizing", "confirmed", "placing"]
    for state in active_states:
        await seed_run(db, household_id, state=state)
    await seed_run(db, household_id, state="completed")   # should NOT match

    resp = await app_client.get("/v1/runs?status=in_progress")
    data = resp.json()["data"]
    assert data["filtered_count"] == len(active_states)
    returned_states = {r["state"] for r in data["runs"]}
    assert "completed" not in returned_states
    for s in active_states:
        assert s in returned_states


@pytest.mark.asyncio
async def test_status_filter_completed(app_client, db):
    """status=completed returns only completed runs."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    await seed_run(db, household_id, state="completed")
    await seed_run(db, household_id, state="failed")
    await seed_run(db, household_id, state="skipped")

    resp = await app_client.get("/v1/runs?status=completed")
    data = resp.json()["data"]
    assert data["filtered_count"] == 1
    assert all(r["state"] == "completed" for r in data["runs"])


@pytest.mark.asyncio
async def test_status_filter_failed(app_client, db):
    """status=failed returns only failed runs."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    await seed_run(db, household_id, state="completed")
    await seed_run(db, household_id, state="failed")

    resp = await app_client.get("/v1/runs?status=failed")
    data = resp.json()["data"]
    assert data["filtered_count"] == 1
    assert data["runs"][0]["state"] == "failed"


@pytest.mark.asyncio
async def test_status_filter_skipped(app_client, db):
    """status=skipped returns only skipped runs."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    await seed_run(db, household_id, state="skipped")
    await seed_run(db, household_id, state="completed")

    resp = await app_client.get("/v1/runs?status=skipped")
    data = resp.json()["data"]
    assert data["filtered_count"] == 1
    assert data["runs"][0]["state"] == "skipped"


@pytest.mark.asyncio
async def test_status_filter_awaiting_confirmation(app_client, db):
    """status=awaiting_confirmation returns only awaiting_confirmation."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    await seed_run(db, household_id, state="awaiting_confirmation")
    await seed_run(db, household_id, state="completed")

    resp = await app_client.get("/v1/runs?status=awaiting_confirmation")
    data = resp.json()["data"]
    assert data["filtered_count"] == 1
    assert data["runs"][0]["state"] == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_status_filter_unknown_returns_empty(app_client, db):
    """An unrecognised status label returns empty list (no 500)."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)
    await seed_run(db, household_id, state="completed")

    resp = await app_client.get("/v1/runs?status=nonexistent_badge")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["runs"]           == []
    assert data["filtered_count"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs — filtered_count vs total_runs
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_filtered_count_reflects_filter(app_client, db):
    """filtered_count matches only runs that pass the filter."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    await seed_run(db, household_id, state="completed")
    await seed_run(db, household_id, state="completed")
    await seed_run(db, household_id, state="failed")

    resp = await app_client.get("/v1/runs?status=completed")
    data = resp.json()["data"]
    assert data["filtered_count"]         == 2
    assert data["stats"]["total_runs"]    == 3   # lifetime total unaffected by filter


@pytest.mark.asyncio
async def test_no_filter_filtered_count_equals_total_runs(app_client, db):
    """Without a filter, filtered_count == stats.total_runs."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    await seed_run(db, household_id, state="completed")
    await seed_run(db, household_id, state="failed")
    await seed_run(db, household_id, state="skipped")

    resp = await app_client.get("/v1/runs")
    data = resp.json()["data"]
    assert data["filtered_count"] == data["stats"]["total_runs"] == 3


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs — stats
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_last_order_total_from_completed_run_order(app_client, db):
    """last_order_total uses Order.grand_total of most recent completed run."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    old_run = await seed_run(db, household_id, state="completed", triggered_offset_days=7)
    await seed_order(db, household_id, old_run, grand_total=400.0)

    new_run = await seed_run(db, household_id, state="completed", triggered_offset_days=0)
    await seed_order(db, household_id, new_run, grand_total=850.0)

    resp = await app_client.get("/v1/runs")
    stats = resp.json()["data"]["stats"]
    assert stats["last_order_total"] == 850.0


@pytest.mark.asyncio
async def test_last_order_total_ignores_failed_runs(app_client, db):
    """Failed/skipped runs (no Order) do not appear as last_order_total."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    completed = await seed_run(db, household_id, state="completed", triggered_offset_days=7)
    await seed_order(db, household_id, completed, grand_total=600.0)

    # More recent but failed — no order
    await seed_run(db, household_id, state="failed", triggered_offset_days=0)

    resp = await app_client.get("/v1/runs")
    stats = resp.json()["data"]["stats"]
    assert stats["last_order_total"] == 600.0


@pytest.mark.asyncio
async def test_avg_order_total_computed_via_sql(app_client, db):
    """avg_order_total is the mean across all completed orders."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    run1 = await seed_run(db, household_id, state="completed", triggered_offset_days=14)
    await seed_order(db, household_id, run1, grand_total=400.0)
    run2 = await seed_run(db, household_id, state="completed", triggered_offset_days=7)
    await seed_order(db, household_id, run2, grand_total=600.0)

    # Failed run with no order — must NOT affect avg
    await seed_run(db, household_id, state="failed")

    resp = await app_client.get("/v1/runs")
    stats = resp.json()["data"]["stats"]
    assert stats["avg_order_total"] == 500.0   # (400 + 600) / 2


@pytest.mark.asyncio
async def test_avg_order_total_null_when_no_completed_runs(app_client, db):
    """avg_order_total is null when there are no completed runs."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    await seed_run(db, household_id, state="failed")
    await seed_run(db, household_id, state="skipped")

    resp = await app_client.get("/v1/runs")
    stats = resp.json()["data"]["stats"]
    assert stats["avg_order_total"]  is None
    assert stats["last_order_total"] is None


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs — next_run_at
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_next_run_at_populated(app_client, db):
    """next_run_at comes from HouseholdPreferences."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    future = datetime.now(timezone.utc) + timedelta(days=5)
    await set_next_run_at(db, household_id, future)

    resp = await app_client.get("/v1/runs")
    d = resp.json()["data"]
    assert d["next_run_at"] is not None
    parsed = datetime.fromisoformat(d["next_run_at"].replace("Z", "+00:00"))
    assert abs((parsed - future).total_seconds()) < 2


@pytest.mark.asyncio
async def test_next_run_at_null_when_not_set(app_client, db):
    """next_run_at is null when prefs.next_run_at is not set."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/runs")
    assert resp.json()["data"]["next_run_at"] is None


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs — pagination
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pagination_offset(app_client, db):
    """offset=1 skips the first (newest) run."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    r1 = await seed_run(db, household_id, state="completed", triggered_offset_days=0)
    r2 = await seed_run(db, household_id, state="skipped",   triggered_offset_days=7)

    resp = await app_client.get("/v1/runs?limit=1&offset=1")
    data = resp.json()["data"]
    assert len(data["runs"]) == 1
    assert data["runs"][0]["id"] == r2.id
    assert data["filtered_count"] == 2   # total hasn't changed


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs — total_price and item_count
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_total_price_uses_order_grand_total_for_completed(app_client, db):
    """For completed runs, total_price = Order.grand_total."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    run = await seed_run(db, household_id, state="completed")
    await seed_item(db, run.id, household_id, price=28.0)   # items_total = 28
    await seed_order(db, household_id, run, grand_total=520.0)  # order total overrides

    resp = await app_client.get("/v1/runs")
    assert resp.json()["data"]["runs"][0]["total_price"] == 520.0


@pytest.mark.asyncio
async def test_total_price_uses_items_sum_for_awaiting(app_client, db):
    """For awaiting_confirmation runs (no Order yet), total_price = sum(LoopRunItem.total_price)."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    run = await seed_run(db, household_id, state="awaiting_confirmation")
    await seed_item(db, run.id, household_id, name="Salt", price=28.0)
    await seed_item(db, run.id, household_id, name="Milk", price=64.0)

    resp = await app_client.get("/v1/runs")
    assert resp.json()["data"]["runs"][0]["total_price"] == 92.0


@pytest.mark.asyncio
async def test_item_count_reflects_loop_run_items(app_client, db):
    """item_count in run list equals the number of LoopRunItems seeded."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    run = await seed_run(db, household_id, state="completed")
    await seed_item(db, run.id, household_id, name="Salt")
    await seed_item(db, run.id, household_id, name="Milk")
    await seed_item(db, run.id, household_id, name="Atta")

    resp = await app_client.get("/v1/runs")
    assert resp.json()["data"]["runs"][0]["item_count"] == 3


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs/{run_id}/items — authentication + ownership
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_run_items_unauthenticated(app_client):
    """No session → NOT_AUTHENTICATED."""
    resp = await app_client.get("/v1/runs/00000000-0000-0000-0000-000000000000/items")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_run_items_not_found(app_client, db):
    """Non-existent run_id → NOT_FOUND."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/runs/00000000-0000-0000-0000-000000000001/items")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_run_items_cross_household_returns_404_not_403(app_client, db):
    """
    Run belongs to a different household → NOT_FOUND (not 403).
    Prevents leaking that the run ID exists at all.
    """
    owner_id = await create_household(db, swiggy_user_id="owner_001")
    other_id = await create_household(db, swiggy_user_id="other_002")
    await auth_session(app_client, other_id)

    run = await seed_run(db, owner_id, state="completed")

    resp = await app_client.get(f"/v1/runs/{run.id}/items")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"


# ══════════════════════════════════════════════════════════════════════════════
# GET /v1/runs/{run_id}/items — response schema
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_run_items_happy_path(app_client, db):
    """Authenticated request for own run returns items with all required fields."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    run  = await seed_run(db, household_id, state="completed")
    await seed_item(db, run.id, household_id, name="Tata Salt", price=28.0)

    resp = await app_client.get(f"/v1/runs/{run.id}/items")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True

    items = body["data"]["items"]
    assert len(items) == 1

    item = items[0]
    required_fields = ["item_name", "swiggy_product_name", "brand", "quantity",
                       "unit", "total_price", "added_by", "is_substitution", "original_item_name"]
    for field in required_fields:
        assert field in item, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_run_items_substitution_fields(app_client, db):
    """Substituted item has is_substitution=True and original_item_name populated."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    run = await seed_run(db, household_id, state="completed")
    await seed_item(
        db, run.id, household_id,
        name              = "Amul Butter",
        is_substitution   = True,
        original_item_name = "Milky Mist Butter",
    )

    resp = await app_client.get(f"/v1/runs/{run.id}/items")
    item = resp.json()["data"]["items"][0]

    assert item["is_substitution"]    is True
    assert item["original_item_name"] == "Milky Mist Butter"


@pytest.mark.asyncio
async def test_run_items_empty_run(app_client, db):
    """A run with no items returns empty list, not an error."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    run = await seed_run(db, household_id, state="failed")

    resp = await app_client.get(f"/v1/runs/{run.id}/items")
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["items"] == []


@pytest.mark.asyncio
async def test_run_items_added_by_preserved(app_client, db):
    """added_by values (rules_engine, llm, user_added) are preserved on return."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    run = await seed_run(db, household_id, state="completed")
    await seed_item(db, run.id, household_id, name="Salt",  added_by="rules_engine")
    await seed_item(db, run.id, household_id, name="Oats",  added_by="llm")
    await seed_item(db, run.id, household_id, name="Honey", added_by="user_added")

    resp = await app_client.get(f"/v1/runs/{run.id}/items")
    items = resp.json()["data"]["items"]
    added_by_values = {i["added_by"] for i in items}
    assert added_by_values == {"rules_engine", "llm", "user_added"}


@pytest.mark.asyncio
async def test_run_items_multiple_items(app_client, db):
    """Multiple items returned for a run with several LoopRunItems."""
    household_id = await create_household(db)
    await auth_session(app_client, household_id)

    run = await seed_run(db, household_id, state="completed")
    for name in ["Salt", "Milk", "Atta", "Dal", "Oil"]:
        await seed_item(db, run.id, household_id, name=name, price=50.0)

    resp = await app_client.get(f"/v1/runs/{run.id}/items")
    items = resp.json()["data"]["items"]
    assert len(items) == 5


# ══════════════════════════════════════════════════════════════════════════════
# Cross-household isolation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_runs_isolated_per_household(app_client, db):
    """GET /runs only returns runs for the authenticated household."""
    owner_id = await create_household(db, swiggy_user_id="iso_owner_001")
    other_id = await create_household(db, swiggy_user_id="iso_other_002")

    await seed_run(db, owner_id, state="completed")
    await seed_run(db, other_id, state="completed")

    await auth_session(app_client, owner_id)
    resp = await app_client.get("/v1/runs")
    data = resp.json()["data"]
    assert data["filtered_count"] == 1
    assert data["stats"]["total_runs"] == 1
