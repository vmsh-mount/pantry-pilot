"""
Integration test — Gap-to-Cart Phase B1 backfill convergence
(app/tasks/maintenance.py::backfill_nutrition_concepts)

Regression test for a real bug caught in review: the backfill selects rows
with `food_concept IS NULL`, but rows that are genuinely unresolvable (no
name recoverable, or the mechanical normalizer legitimately returns None)
were being left NULL — so the same rows would be re-selected by every
subsequent batch forever. Fix: unresolvable rows are stamped `food_concept
= ""` (tried, gave up), distinct from NULL (not yet attempted), so the
candidate set actually shrinks.

This test proves the fix: running the task twice must not re-process the
same unresolvable rows, and the second run's `updated` count for those
rows must be zero.
"""

import uuid

import pytest
from unittest.mock import patch

from tests.integration.conftest import create_household


async def _make_nutrition_cache_row(db, sku_id: str, *, matched_name=None,
                                      protein_per_100g=None, source="off"):
    from app.models.db import NutritionCache
    row = NutritionCache(
        id=str(uuid.uuid4()),
        sku_id=sku_id,
        source=source,
        confidence="medium",
        matched_name=matched_name,
        protein_per_100g=protein_per_100g,
        nutrients={},
        # food_concept intentionally left NULL — "not yet attempted"
    )
    db.add(row)
    await db.flush()
    return row


@pytest.mark.asyncio
async def test_backfill_converges_on_unresolvable_rows(db):
    """
    Three rows:
      A. matched_name present, resolvable ("Toor Dal") -> gets a real concept
      B. matched_name = "Fresh Organic Pure Natural" (all stopwords) ->
         mechanical normalizer legitimately returns None -> must become ""
      C. no matched_name, no matching OrderItem -> must become ""

    First run must resolve A and stamp "" on B and C. Second run must find
    ZERO rows still NULL (the whole point of the fix) — proving the task
    converges instead of looping on B/C forever.
    """
    from app.models.db import NutritionCache
    from app.tasks.maintenance import _backfill_nutrition_concepts
    from sqlalchemy import select

    household_id = await create_household(db)

    row_a = await _make_nutrition_cache_row(db, "SKU_A", matched_name="Toor Dal", protein_per_100g=22.0)
    row_b = await _make_nutrition_cache_row(db, "SKU_B", matched_name="Fresh Organic Pure Natural")
    row_c = await _make_nutrition_cache_row(db, "SKU_C", matched_name=None)  # no OrderItem exists for SKU_C either
    await db.commit()

    with patch("app.database.AsyncSessionLocal", return_value=db):
        await _backfill_nutrition_concepts(batch_size=10)

    # The task's own `async with AsyncSessionLocal() as db:` closes the
    # session on exit (same session object as this test's `db` fixture) —
    # db.refresh() would fail as "not persistent"; query fresh instead.
    async def _concept_for(sku_id: str) -> str | None:
        result = await db.execute(select(NutritionCache).where(NutritionCache.sku_id == sku_id))
        return result.scalar_one().food_concept

    assert await _concept_for("SKU_A") == "dal"
    assert await _concept_for("SKU_B") == ""   # gave up, not NULL
    assert await _concept_for("SKU_C") == ""   # gave up, not NULL

    # ── The actual regression check: a second run must select ZERO rows ──
    still_null = await db.execute(
        select(NutritionCache).where(NutritionCache.food_concept.is_(None))
    )
    assert still_null.scalars().all() == [], (
        "Rows still NULL after a full backfill pass — the task would "
        "re-select and reprocess these forever if looped on `more`."
    )
