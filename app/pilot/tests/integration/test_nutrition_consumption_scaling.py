"""
Integration test — nutrition consumption scaling
(tasks/features/nutrition-consumed-not-purchased.md)

Exercises the real batch PantryItem query added to resolve_order_nutrition
plus estimate_consumed_g/compute_item_totals against a real Postgres-backed
PantryItem row — specifically to catch any Decimal-vs-float coercion issue
from the Numeric(8,3) column, which a pure-Python unit test using plain
floats can't catch.

Deliberately does not invoke the resolve_order_nutrition Celery task itself
(it wraps its logic in asyncio.run(), which cannot be called from within
pytest-asyncio's already-running event loop) — instead reproduces the exact
query + call sequence the task performs, against the real test DB.
"""

import uuid

import pytest
from sqlalchemy import select

from tests.integration.conftest import create_household


async def _make_order(db, household_id):
    from app.models.db import Order
    order = Order(
        id=str(uuid.uuid4()),
        household_id=household_id,
        swiggy_order_id=f"order_{uuid.uuid4().hex[:8]}",
        swiggy_address_id="addr_home_001",
        item_total=100,
        grand_total=100,
    )
    db.add(order)
    return order


async def _make_order_item(db, household_id, order_id, product_name, quantity, unit):
    from app.models.db import OrderItem
    item = OrderItem(
        id=str(uuid.uuid4()),
        order_id=order_id,
        household_id=household_id,
        swiggy_sku_id=f"sku_{product_name}",
        product_name=product_name,
        quantity=quantity,
        unit=unit,
        unit_price=100,
        total_price=100,
    )
    db.add(item)
    return item


async def _make_pantry_item(db, household_id, item_name, avg_weekly_consumption, standard_unit):
    from app.models.db import PantryItem
    item = PantryItem(
        id=str(uuid.uuid4()),
        household_id=household_id,
        item_name=item_name,
        category="staples",
        standard_unit=standard_unit,
        reorder_threshold=1,
        avg_weekly_consumption=avg_weekly_consumption,
    )
    db.add(item)
    return item


_RICE_RESOLVED = {
    "quantity_unresolvable": False,
    "quantity_g": 5000,
    "calories_per_100g": 350,
    "protein_per_100g": 7.0,
    "total_carbs_per_100g": 78.0,
    "fat_per_100g": 0.5,
    "fiber_per_100g": 1.0,
    "sodium_mg_per_100g": 5.0,
    "nutrients": {},
}


@pytest.mark.asyncio
async def test_bulk_pack_capped_by_real_pantry_consumption_rate(db):
    """
    A household with a learned consumption rate for a 5kg rice bag should
    have this order's nutrition capped at the estimated weekly consumption,
    not the full pack — reproducing the exact query + scaling path
    resolve_order_nutrition now uses.
    """
    from app.services.nutrition_resolution import compute_item_totals, estimate_consumed_g
    from app.models.db import PantryItem

    household_id = await create_household(db)
    order = await _make_order(db, household_id)
    await db.flush()

    product_name = "India Gate Basmati Rice 5kg"
    await _make_order_item(db, household_id, order.id, product_name, quantity=5, unit="kg")
    await _make_pantry_item(db, household_id, product_name, avg_weekly_consumption=1.2, standard_unit="kg")
    await db.commit()

    # Exactly the batch query resolve_order_nutrition runs.
    pantry_result = await db.execute(
        select(PantryItem).where(
            PantryItem.household_id == household_id,
            PantryItem.item_name.in_([product_name]),
        )
    )
    pantry_item = pantry_result.scalar_one()

    consumed_g = estimate_consumed_g(
        quantity_g=_RICE_RESOLVED["quantity_g"],
        avg_weekly_consumption=float(pantry_item.avg_weekly_consumption),
        consumption_unit=pantry_item.standard_unit,
        item_name=product_name,
    )
    scaled = compute_item_totals(_RICE_RESOLVED, consumed_g)

    # 1.2kg/week learned rate -> capped at 1200g, not the full 5000g pack.
    assert scaled["consumed_g"] == 1200
    assert scaled["pack_quantity_g"] == 5000
    assert scaled["calories"] == 350 * 12   # 1200g / 100
    assert scaled["calories"] != 350 * 50   # the old, distorted full-pack number


@pytest.mark.asyncio
async def test_first_time_item_falls_back_to_full_pack(db):
    """No PantryItem exists yet for this exact product name — resolves
    without error and matches today's (pre-fix) full-pack behavior."""
    from app.services.nutrition_resolution import compute_item_totals, estimate_consumed_g
    from app.models.db import PantryItem

    household_id = await create_household(db)
    order = await _make_order(db, household_id)
    await db.flush()

    product_name = "Tata Sampann Toor Dal 1kg"
    await _make_order_item(db, household_id, order.id, product_name, quantity=1, unit="kg")
    await db.commit()

    pantry_result = await db.execute(
        select(PantryItem).where(
            PantryItem.household_id == household_id,
            PantryItem.item_name.in_([product_name]),
        )
    )
    pantry_item = pantry_result.scalar_one_or_none()
    assert pantry_item is None

    resolved = {
        "quantity_unresolvable": False,
        "quantity_g": 1000,
        "calories_per_100g": 340,
        "protein_per_100g": 24.0,
        "total_carbs_per_100g": 60.0,
        "fat_per_100g": 1.0,
        "fiber_per_100g": 15.0,
        "sodium_mg_per_100g": 10.0,
        "nutrients": {},
    }

    consumed_g = estimate_consumed_g(
        quantity_g=resolved["quantity_g"],
        avg_weekly_consumption=None,
        consumption_unit=None,
        item_name=product_name,
    )
    scaled = compute_item_totals(resolved, consumed_g)

    assert scaled["consumed_g"] == 1000
    assert scaled["calories"] == 340 * 10
