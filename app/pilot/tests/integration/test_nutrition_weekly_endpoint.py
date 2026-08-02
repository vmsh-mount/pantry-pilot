"""
Integration test — GET /v1/nutrition/weekly (real-Postgres regression guard).

No test previously exercised this endpoint against real order_nutrition +
orders data. It silently 500'd on any household with actual nutrition
history: three separate func.date_trunc(...) calls in SELECT/GROUP BY/
ORDER BY each compile to their own bound parameter, so Postgres treats them
as different expressions (even with identical literal args) and rejects
"column must appear in the GROUP BY clause." Invisible under the unit-test
SQLite path (SQLite is lenient about GROUP BY expression matching) — only
reproduces against real Postgres, which is exactly why this integration
test (using the `db` fixture's real-Postgres-per-test setup) is the right
place for the regression guard, not a unit test.

Found via manual visual verification of the Gap-to-Cart Phase B4 digest
page, which calls this endpoint directly.
"""

import uuid
from datetime import datetime, timezone

import pytest

from tests.integration.conftest import create_household, set_session, enable_nutrition_gaps


async def _add_order_nutrition(db, household_id: str, calories: float, protein_g: float):
    from app.models.db import Order, OrderNutrition
    order = Order(
        id=str(uuid.uuid4()), household_id=household_id,
        swiggy_order_id=f"order_{uuid.uuid4().hex[:8]}", swiggy_address_id="addr_home_001",
        item_total=100, grand_total=100, placed_at=datetime.now(timezone.utc),
    )
    db.add(order)
    await db.flush()
    on = OrderNutrition(
        id=str(uuid.uuid4()), order_id=order.id, household_id=household_id,
        total_calories=calories, total_protein_g=protein_g,
        total_carbs_g=0, total_fat_g=0, total_fiber_g=0, total_sodium_mg=0,
        total_items=1, resolved_items=1, item_breakdown=[],
    )
    db.add(on)
    await db.flush()
    await db.commit()


@pytest.mark.asyncio
async def test_weekly_endpoint_does_not_500_with_multiple_orders(app_client, db):
    """The GROUP BY regression only reproduces with >=1 row actually grouped
    against real Postgres — a single order_nutrition row is enough to trigger
    it (the bug is about expression identity, not row count)."""
    household_id = await create_household(db)
    await enable_nutrition_gaps(db, household_id)
    await _add_order_nutrition(db, household_id, calories=500, protein_g=30)
    await _add_order_nutrition(db, household_id, calories=600, protein_g=25)

    set_session(app_client, household_id)
    resp = await app_client.get("/v1/nutrition/weekly?weeks=1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True, f"endpoint failed: {body}"
    weeks = body["data"]["weeks"]
    assert len(weeks) == 1
    assert weeks[0]["total_calories"] == 1100
    assert weeks[0]["total_protein_g"] == 55
    assert weeks[0]["order_count"] == 2


@pytest.mark.asyncio
async def test_weekly_endpoint_empty_history_returns_empty_weeks(app_client, db):
    household_id = await create_household(db)
    await enable_nutrition_gaps(db, household_id)
    set_session(app_client, household_id)
    resp = await app_client.get("/v1/nutrition/weekly?weeks=1")
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["weeks"] == []
