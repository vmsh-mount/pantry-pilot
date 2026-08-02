"""
Unit tests — non-food gate (tasks/features/nutrition-non-food-gate.md)

Covers:
  1. _is_non_food: positive matches across the curated vocabulary, negative
     on real food (including items sharing the "grocery" bucket with
     personal-care/cleaning SKUs — soya chunks, chicken, tofu), and a
     word-boundary check.
  2. _estimate_llm: a model response with {"not_food": true} is translated
     to the not_food_signal sentinel, independent of whether the keyword
     gate would have caught the item first (this is the defense-in-depth
     layer, tested in isolation).

resolve_item's short-circuit behavior (the actual gate placement) needs a
real DB + Redis for _cache_not_food's write path, so that's covered as an
integration test instead — see
tests/integration/test_nutrition_non_food_gate.py.
"""

import json

import pytest
from unittest.mock import AsyncMock, patch

from app.services.nutrition_resolution import _is_non_food, _estimate_llm


# ── 1. _is_non_food ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("item_name,brand", [
    ("Dove Soap", "Dove"),
    ("Dove Shampoo", "Dove"),
    ("Surf Excel Detergent", "Surf Excel"),
    ("Colgate Strong Teeth Toothpaste", "Colgate"),
    ("Vim Dishwash Bar", "Vim"),
    ("Dettol Handwash", "Dettol"),
    ("Listerine Mouthwash", "Listerine"),
    ("Odonil Air Freshener", "Odonil"),
    ("Huggies Diaper", "Huggies"),
])
def test_is_non_food_matches_curated_vocabulary(item_name, brand):
    assert _is_non_food(item_name, brand) is True


@pytest.mark.parametrize("item_name,brand", [
    ("India Gate Basmati Rice", "India Gate"),
    ("Amul Toned Milk", "Amul"),
    ("Nutrela Soya Chunks", "Nutrela"),          # same "grocery" bucket as soap
    ("Licious Chicken Breast", "Licious"),        # same "grocery" bucket as soap
    ("Epigamia Tofu", "Epigamia"),                # same "grocery" bucket as soap
    ("Tomato", None),
    ("Medjoul Dates", None),
    ("Whole Almonds", None),
])
def test_is_non_food_does_not_flag_real_food(item_name, brand):
    assert _is_non_food(item_name, brand) is False


def test_is_non_food_is_word_boundary_matched_not_substring():
    # Guards against a future vocabulary addition being substring-matched
    # by accident, the same convention already established for the pantry
    # page's ITEM_ICON_KEYWORDS.
    assert _is_non_food("Soapstone Serving Tray", None) is False


# ── 2. _estimate_llm not_food_signal translation ─────────────────────────────

@pytest.mark.asyncio
async def test_estimate_llm_translates_not_food_response_to_sentinel():
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=json.dumps({"not_food": True}))
    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        resolved = await _estimate_llm("Surf Excel Detergent", "Surf Excel", "1 kg")
    assert resolved == {"not_food_signal": True}


@pytest.mark.asyncio
async def test_estimate_llm_ignores_not_food_false():
    # A response that explicitly says not_food: false should resolve
    # normally, not be mistaken for the sentinel.
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=json.dumps({
        "not_food": False,
        "calories_per_100g": 60, "protein_per_100g": 3.2,
        "total_carbs_per_100g": 4.8, "fat_per_100g": 3.3,
    }))
    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        resolved = await _estimate_llm("Milk", "Amul", "500ml")
    assert resolved.get("not_food_signal") is None
    assert resolved["calories_per_100g"] == 60
