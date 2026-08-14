"""
Integration tests — non-food gate, resolve_item short-circuit
(tasks/features/nutrition-non-food-gate.md)

resolve_item's gate placement (before Redis, before DB, before OFF/USDA/LLM)
needs a real DB + Redis for _cache_not_food's write path — that's what
distinguishes this from the pure-logic unit tests in
tests/unit/test_nutrition_non_food_gate.py.
"""

import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select

from app.models.db import NutritionCache
from app.services.nutrition_resolution import resolve_item

# _reset_redis_singleton (the fix for the module-level Redis client being
# bound to a stale event loop across tests) now lives as an autouse fixture
# in tests/integration/conftest.py — promoted there once a second, unrelated
# test file hit the identical failure. No longer needed locally here.


@pytest.mark.asyncio
async def test_non_food_item_never_reaches_off_usda_or_llm(db):
    with (
        patch("app.services.nutrition_resolution._search_off", new=AsyncMock()) as mock_off,
        patch("app.services.nutrition_resolution._search_usda", new=AsyncMock()) as mock_usda,
        patch("app.services.nutrition_resolution._estimate_llm", new=AsyncMock()) as mock_llm,
    ):
        resolved = await resolve_item(
            db=db, sku_id="sku_soap_001", item_name="Dove Soap",
            brand="Dove", qty_desc="125 g",
        )

    mock_off.assert_not_called()
    mock_usda.assert_not_called()
    mock_llm.assert_not_called()
    assert resolved["confidence"] == "not_food"
    assert resolved["source"] == "not_food"
    assert resolved["calories_per_100g"] is None


@pytest.mark.asyncio
async def test_non_food_determination_is_cached_in_db(db):
    await resolve_item(
        db=db, sku_id="sku_detergent_001", item_name="Surf Excel Detergent",
        brand="Surf Excel", qty_desc="1 kg",
    )
    row = (await db.execute(
        select(NutritionCache).where(NutritionCache.sku_id == "sku_detergent_001")
    )).scalar_one()
    assert row.confidence == "not_food"
    assert row.source == "not_food"


@pytest.mark.asyncio
async def test_grocery_bucket_food_item_is_not_gated(db):
    """Soya chunks share the pantry's "grocery" category bucket with soap
    and detergent, but must resolve normally — proves the gate discriminates
    on the item itself, not the coarse category bucket."""
    fake_off_result = {
        "source": "off", "confidence": "medium", "serving_size_g": None,
        "calories_per_100g": 345, "protein_per_100g": 52.0,
        "total_carbs_per_100g": 33.0, "fat_per_100g": 0.5,
        "fiber_per_100g": 13.0, "sodium_mg_per_100g": 20.0,
        "nutrients": {}, "nutriscore_grade": None,
        "matched_name": "Nutrela Soya Chunks", "off_product_id": "off_123",
        "raw_data": {"source": "off"},
    }
    with patch("app.services.nutrition_resolution._search_off", new=AsyncMock(return_value=fake_off_result)):
        resolved = await resolve_item(
            db=db, sku_id="sku_soya_001", item_name="Nutrela Soya Chunks",
            brand="Nutrela", qty_desc="200 g",
        )

    assert resolved["confidence"] == "medium"
    assert resolved["source"] == "off"
    assert resolved["calories_per_100g"] == 345
