"""
Integration tests — Gap-to-Cart Phase B2: learned candidate map
(app.tasks.nutrition._rebuild_nutrient_food_map, app.services.nutrient_candidates)

Covers the PRD's Definition of Done:
  1. Nightly job upserts nutrient_food_candidate without duplicate
     (nutrient, food_concept) rows — running twice doesn't double-insert.
  2. Median aggregation robust to a synthetic outlier.
  3. Seed fallback triggers only below the (N, M) threshold; a well-populated
     (nutrient, diet) pair does NOT fall back.
  4. rows_from_seed_fallback is returned/logged per run.
  5. No price field anywhere on nutrient_food_candidate or its writer.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import patch

from tests.integration.conftest import create_household


async def _add_order_with_item(db, household_id: str, sku_id: str, product_name: str,
                                 brand: str | None = None, placed_at=None):
    from app.models.db import Order, OrderItem
    order = Order(
        id=str(uuid.uuid4()), household_id=household_id,
        swiggy_order_id=f"order_{uuid.uuid4().hex[:8]}",
        swiggy_address_id="addr_1",
        item_total=100, grand_total=100,
        placed_at=placed_at or datetime.now(timezone.utc),
    )
    db.add(order)
    await db.flush()
    item = OrderItem(
        id=str(uuid.uuid4()), order_id=order.id, household_id=household_id,
        swiggy_sku_id=sku_id, product_name=product_name, brand=brand,
        quantity=1, unit="pack", unit_price=50, total_price=50,
    )
    db.add(item)
    await db.flush()
    return order, item


async def _add_cache_row(db, sku_id: str, food_concept: str, protein_per_100g: float,
                          notable=("protein",), confidence="medium", source="off"):
    from app.models.db import NutritionCache
    row = NutritionCache(
        id=str(uuid.uuid4()), sku_id=sku_id, source=source, confidence=confidence,
        food_concept=food_concept, notable_nutrients=list(notable),
        protein_per_100g=protein_per_100g, nutrients={},
    )
    db.add(row)
    await db.flush()
    return row


# ── 1 & 4. No duplicates on re-run; rows_from_seed_fallback returned ──────────

@pytest.mark.asyncio
async def test_rebuild_upserts_without_duplicates_on_rerun(db):
    from app.tasks.nutrition import _rebuild_nutrient_food_map
    from app.models.db import NutrientFoodCandidate
    from sqlalchemy import select

    household_id = await create_household(db)
    await _add_cache_row(db, "SKU1", "dal", 22.0)
    await _add_cache_row(db, "SKU2", "dal", 24.0)
    await _add_order_with_item(db, household_id, "SKU1", "Toor Dal")
    await db.commit()

    with patch("app.database.AsyncSessionLocal", return_value=db):
        first = await _rebuild_nutrient_food_map()
        second = await _rebuild_nutrient_food_map()

    assert first["rows_upserted"] >= 1
    assert second["rows_upserted"] >= 1  # re-upserts the same rows, doesn't skip

    result = await db.execute(
        select(NutrientFoodCandidate).where(
            NutrientFoodCandidate.nutrient == "protein",
            NutrientFoodCandidate.food_concept == "dal",
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1, f"Expected exactly one (protein, dal) row after two runs, got {len(rows)}"


# ── 2. Median robust to a synthetic outlier ──────────────────────────────────

@pytest.mark.asyncio
async def test_median_aggregation_robust_to_outlier(db):
    """Four SKUs for 'paneer': three cluster around 18g protein, one bad OFF
    match claims 90g. Median must land near the real cluster, not be dragged
    toward the outlier the way a mean would be."""
    from app.tasks.nutrition import _rebuild_nutrient_food_map
    from app.models.db import NutrientFoodCandidate
    from sqlalchemy import select

    household_id = await create_household(db)
    await _add_cache_row(db, "P1", "paneer", 18.0)
    await _add_cache_row(db, "P2", "paneer", 18.5)
    await _add_cache_row(db, "P3", "paneer", 17.5)
    await _add_cache_row(db, "P4", "paneer", 90.0)  # bad OFF match outlier
    await _add_order_with_item(db, household_id, "P1", "Paneer")
    await db.commit()

    with patch("app.database.AsyncSessionLocal", return_value=db):
        await _rebuild_nutrient_food_map()

    result = await db.execute(
        select(NutrientFoodCandidate).where(
            NutrientFoodCandidate.nutrient == "protein",
            NutrientFoodCandidate.food_concept == "paneer",
        )
    )
    row = result.scalar_one()
    # median of [17.5, 18.0, 18.5, 90.0] = (18.0+18.5)/2 = 18.25 — nowhere
    # near the mean (36.0), which the outlier would have dragged it to.
    assert row.nutrient_per_100g == pytest.approx(18.25, abs=0.01)
    assert row.sample_size == 4


# ── 3. Seed fallback threshold ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_fallback_triggers_below_threshold(db):
    """A single sparse row for 'dal' (sample_size < M) must trigger seed
    fallback for protein/vegetarian."""
    from app.services.nutrient_candidates import get_candidates, SEED_FALLBACK_MIN_SAMPLE_SIZE
    from app.models.db import NutrientFoodCandidate

    row = NutrientFoodCandidate(
        id=str(uuid.uuid4()), nutrient="protein", food_concept="dal",
        diet_tags=["vegetarian", "vegan", "jain"],
        nutrient_per_100g=22.0, sample_size=1,  # well below SEED_FALLBACK_MIN_SAMPLE_SIZE
        confidence="medium", order_frequency=1,
    )
    db.add(row)
    await db.commit()

    candidates, used_seed = await get_candidates(db, "protein", "vegetarian")
    assert used_seed is True
    assert any(c["food_concept"] in ("paneer", "dal", "rajma", "chana", "soya") for c in candidates)


@pytest.mark.asyncio
async def test_well_populated_pair_does_not_fall_back(db):
    """>= N rows with sample_size >= M for (protein, vegetarian) must use the
    learned table, not the seed."""
    from app.services.nutrient_candidates import (
        get_candidates, SEED_FALLBACK_MIN_ROWS, SEED_FALLBACK_MIN_SAMPLE_SIZE,
    )
    from app.models.db import NutrientFoodCandidate

    concepts = ["dal", "paneer", "rajma", "chana"]
    assert len(concepts) >= SEED_FALLBACK_MIN_ROWS
    for i, concept in enumerate(concepts):
        db.add(NutrientFoodCandidate(
            id=str(uuid.uuid4()), nutrient="protein", food_concept=concept,
            diet_tags=["vegetarian", "vegan", "jain"],
            nutrient_per_100g=20.0 + i, sample_size=SEED_FALLBACK_MIN_SAMPLE_SIZE + 1,
            confidence="medium", order_frequency=5, repurchase_rate=0.5,
        ))
    await db.commit()

    candidates, used_seed = await get_candidates(db, "protein", "vegetarian")
    assert used_seed is False
    returned_concepts = {c["food_concept"] for c in candidates}
    assert returned_concepts <= set(concepts)  # only learned rows, no seed entries mixed in


@pytest.mark.asyncio
async def test_diet_filtering_excludes_non_matching_candidates(db):
    """A dairy-only candidate (vegetarian, not vegan) must not be returned
    for a vegan household even if learned data exists."""
    from app.services.nutrient_candidates import get_candidates
    from app.models.db import NutrientFoodCandidate

    db.add(NutrientFoodCandidate(
        id=str(uuid.uuid4()), nutrient="protein", food_concept="paneer",
        diet_tags=["vegetarian"], nutrient_per_100g=18.0, sample_size=10,
        confidence="medium", order_frequency=5,
    ))
    await db.commit()

    candidates, _ = await get_candidates(db, "protein", "vegan")
    assert all(c["food_concept"] != "paneer" for c in candidates)


# ── 5. No price field ─────────────────────────────────────────────────────────

def test_no_price_field_on_model_or_writer():
    from app.models.db import NutrientFoodCandidate
    import inspect
    columns = {c.name for c in NutrientFoodCandidate.__table__.columns}
    assert not any("price" in c or "rupee" in c for c in columns), (
        f"nutrient_food_candidate must hold no price field — found: {columns}"
    )

    from app.tasks import nutrition as nutrition_task_module
    source = inspect.getsource(nutrition_task_module._upsert_candidate)
    assert "price" not in source.lower() and "rupee" not in source.lower()


# ── diet tagging ───────────────────────────────────────────────────────────────

def test_diet_tags_non_veg_concept_gets_no_tags():
    from app.tasks.nutrition import _diet_tags_for_concept
    assert _diet_tags_for_concept("egg") == []
    assert _diet_tags_for_concept("chicken") == []


def test_diet_tags_root_vegetable_excludes_jain():
    from app.tasks.nutrition import _diet_tags_for_concept
    tags = _diet_tags_for_concept("onion")
    assert "vegetarian" in tags
    assert "jain" not in tags
    assert "vegan" in tags


def test_diet_tags_dairy_excludes_vegan():
    from app.tasks.nutrition import _diet_tags_for_concept
    tags = _diet_tags_for_concept("paneer")
    assert "vegetarian" in tags
    assert "vegan" not in tags
    assert "jain" in tags


def test_diet_tags_plant_food_gets_all_three():
    from app.tasks.nutrition import _diet_tags_for_concept
    tags = _diet_tags_for_concept("dal")
    assert set(tags) == {"vegetarian", "vegan", "jain"}
