"""
Unit tests — Gap-to-Cart Phase B1: SKU enrichment (nutrition_resolution.py)

Covers:
  1. NUTRIENT_KEYS coverage: every density key is actually producible by at
     least one resolution source (guards the silent-empty-join failure mode
     called out in the Phase B1 PRD).
  2. _mechanical_food_concept: brand/descriptor/quantity/parenthetical
     stripping, singularization, empty-input handling.
  3. _mechanical_notable_nutrients: floor-based candidacy, never asserts a
     nutrient with no value.
  4. _estimate_llm: food_concept + notable_nutrients parsing and validation
     (allowed vocab, non-null requirement, floor backstop, mechanical
     fallback when the model's answer doesn't survive validation).
"""

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.nutrition_resolution import (
    NUTRIENT_KEYS,
    _ALLOWED_NOTABLE_KEYS,
    _mechanical_food_concept,
    _mechanical_notable_nutrients,
    _search_off,
    _search_usda,
    _estimate_llm,
)


def _mock_response(body: dict, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body
    return resp


# ── 1. NUTRIENT_KEYS coverage across sources ─────────────────────────────────

_OFF_PRODUCT = {
    "product_name": "Full Cream Milk", "brands": "Nandini",
    "nutriscore_grade": "b",
    "serving_size": "100g",
    "nutriments": {
        "energy-kcal_100g": 60, "proteins_100g": 3.2, "carbohydrates_100g": 4.7,
        "fat_100g": 3.5, "fiber_100g": 0, "sodium_100g": 0.05,
        "sugars_100g": 4.7, "saturated-fat_100g": 2.1, "salt_100g": 0.13,
    },
}

_USDA_FOOD = {
    "fdcId": 12345, "description": "Milk, whole",
    "foodNutrients": [
        {"nutrientName": "Energy", "value": 61},
        {"nutrientName": "Protein", "value": 3.2},
        {"nutrientName": "Carbohydrate, by difference", "value": 4.8},
        {"nutrientName": "Total lipid (fat)", "value": 3.3},
        {"nutrientName": "Fiber, total dietary", "value": 0},
        {"nutrientName": "Sodium, Na", "value": 40},
        {"nutrientName": "Sugars, total including NLEA", "value": 4.8},
        {"nutrientName": "Calcium, Ca", "value": 120},
        {"nutrientName": "Iron, Fe", "value": 0.1},
        {"nutrientName": "Potassium, K", "value": 150},
        {"nutrientName": "Vitamin C, total ascorbic acid", "value": 0},
    ],
}

_LLM_FULL_JSON = """{
  "calories_per_100g": 60, "protein_per_100g": 3.2, "total_carbs_per_100g": 4.8,
  "sugar_per_100g": 4.8, "fat_per_100g": 3.3, "saturated_fat_per_100g": 2.0,
  "fiber_per_100g": 0, "sodium_mg_per_100g": 40,
  "calcium_per_100g": 120, "iron_per_100g": 0.1,
  "potassium_per_100g": 150, "vitamin_c_per_100g": 0,
  "vitamin_b12_per_100g": 0.4, "vitamin_d_per_100g": 1.2,
  "food_concept": "milk", "notable_nutrients": ["protein", "b12", "vitamin_d"]
}"""


@pytest.mark.asyncio
async def test_nutrient_keys_density_fields_all_producible_by_some_source():
    """Every NUTRIENT_KEYS density key must appear in at least one source's
    resolved payload — this is the guard against the PRD's silent-empty-join
    failure mode (a grouping key that no source can ever populate)."""
    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=_mock_response({"products": [_OFF_PRODUCT]})
        )
        off = await _search_off("Nandini Full Cream Milk", "Nandini")

    with patch("httpx.AsyncClient") as mock_http, \
         patch("app.services.nutrition_resolution.get_settings", return_value=MagicMock(usda_api_key="fake")):
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=_mock_response({"foods": [_USDA_FOOD]})
        )
        usda = await _search_usda("Milk")

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=_LLM_FULL_JSON)
    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        llm = await _estimate_llm("Milk", "Nandini", "500ml")

    producible_keys: set[str] = set()
    for resolved in (off, usda, llm):
        producible_keys |= set(resolved.keys())
        producible_keys |= set((resolved.get("nutrients") or {}).keys())

    missing = []
    for nutrient, spec in NUTRIENT_KEYS.items():
        if spec["density"] not in producible_keys:
            missing.append((nutrient, spec["density"]))

    assert not missing, f"NUTRIENT_KEYS density fields with no producing source: {missing}"


@pytest.mark.asyncio
async def test_coverage_matches_prd_table_b12_and_vitamin_d_are_llm_only():
    """Sanity check on the PRD's explicit coverage claim: b12/vitamin_d must
    NOT appear in OFF or USDA payloads — only the LLM path produces them."""
    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=_mock_response({"products": [_OFF_PRODUCT]})
        )
        off = await _search_off("Milk", "Nandini")
    off_keys = set(off.keys()) | set((off.get("nutrients") or {}).keys())
    assert "vitamin_b12_per_100g" not in off_keys
    assert "vitamin_d_per_100g" not in off_keys

    with patch("httpx.AsyncClient") as mock_http, \
         patch("app.services.nutrition_resolution.get_settings", return_value=MagicMock(usda_api_key="fake")):
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=_mock_response({"foods": [_USDA_FOOD]})
        )
        usda = await _search_usda("Milk")
    usda_keys = set(usda.keys()) | set((usda.get("nutrients") or {}).keys())
    assert "vitamin_b12_per_100g" not in usda_keys
    assert "vitamin_d_per_100g" not in usda_keys


# ── 2. _mechanical_food_concept ───────────────────────────────────────────────

@pytest.mark.parametrize("name,brand,expected", [
    ("Nandini Pasteurised Toned Milk", "Nandini", "milk"),
    ("Milking A2 pasteurised milk", "MILKING", "milk"),
    ("Nandini Shubham Milk", "Nandini", "milk"),
    ("Country Delight 30g Protein Buffalo Milk", "Country Delight", "milk"),
    ("Heritage Daily Health Toned Milk", "Heritage", "milk"),
    ("Fresh Eggs White eggs", "Fresh Eggs", "egg"),
    ("NOICE High Protein Eggs (Nut & Bean Feed)", "NOICE", "egg"),
    ("Milky Mist Paneer", "Milky Mist", "paneer"),
    ("Toor Dal 1 kg", "Tata Sampann", "dal"),
    ("Nandini Curd 400g", "Nandini", "curd"),
])
def test_mechanical_food_concept_real_examples(name, brand, expected):
    assert _mechanical_food_concept(name, brand) == expected


def test_mechanical_food_concept_empty_name_returns_none():
    assert _mechanical_food_concept("", "Brand") is None
    assert _mechanical_food_concept(None, "Brand") is None


def test_mechanical_food_concept_all_stopwords_returns_none():
    """If everything strips away (all descriptors, no brand match), return
    None rather than a garbage concept — nullable column, B2 skips it."""
    assert _mechanical_food_concept("Fresh Organic Pure Natural", None) is None


def test_mechanical_food_concept_no_brand_still_works():
    assert _mechanical_food_concept("Toor Dal", None) == "dal"


# ── 3. _mechanical_notable_nutrients ──────────────────────────────────────────

def test_mechanical_notable_nutrients_floor_based():
    resolved = {
        "protein_per_100g": 6.0,   # >= 5.0 floor -> notable
        "fiber_per_100g": 1.0,     # < 3.0 floor -> not notable
        "nutrients": {
            "iron_per_100g": 2.5,      # >= 2.0 floor -> notable
            "calcium_per_100g": 50.0,  # < 100.0 floor -> not notable
        },
    }
    result = _mechanical_notable_nutrients(resolved)
    assert set(result) == {"protein", "iron"}


def test_mechanical_notable_nutrients_never_asserts_missing_value():
    """No density value present at all -> never notable, regardless of floor."""
    resolved = {"nutrients": {}}
    assert _mechanical_notable_nutrients(resolved) == []


def test_mechanical_notable_nutrients_excludes_sodium_sugar_saturated_fat():
    """These are ceilings/limits, not benefits — never candidates, even with
    a very high value."""
    resolved = {
        "sodium_mg_per_100g": 5000,  # very high
        "nutrients": {"sugar_per_100g": 90, "saturated_fat_per_100g": 50},
    }
    assert _mechanical_notable_nutrients(resolved) == []
    assert "sodium" not in _ALLOWED_NOTABLE_KEYS
    assert "sugar" not in _ALLOWED_NOTABLE_KEYS
    assert "saturated_fat" not in _ALLOWED_NOTABLE_KEYS


# ── 4. _estimate_llm: food_concept + notable_nutrients validation ────────────

@pytest.mark.asyncio
async def test_estimate_llm_uses_model_food_concept_when_present():
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=_LLM_FULL_JSON)
    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        resolved = await _estimate_llm("Milk", "Nandini", "500ml")
    assert resolved["food_concept"] == "milk"


@pytest.mark.asyncio
async def test_estimate_llm_falls_back_to_mechanical_concept_when_model_omits_it():
    json_without_concept = _LLM_FULL_JSON.replace('"food_concept": "milk",', '"food_concept": null,')
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=json_without_concept)
    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        resolved = await _estimate_llm("Nandini Toned Milk", "Nandini", "500ml")
    assert resolved["food_concept"] == "milk"  # mechanical fallback on item_name


@pytest.mark.asyncio
async def test_estimate_llm_notable_nutrients_rejects_disallowed_key():
    """Model claims 'sodium' as notable — must be stripped even though it's a
    key the model actually populated a value for (sodium is a ceiling, not
    a benefit, and is not in the allowed vocabulary). Bump protein above its
    floor (base fixture's 3.2 is below the 5.0 floor) so this test isolates
    vocabulary rejection from floor rejection — floor rejection has its own
    dedicated test below."""
    bad_json = _LLM_FULL_JSON.replace(
        '"protein_per_100g": 3.2,', '"protein_per_100g": 8.0,'
    ).replace(
        '"notable_nutrients": ["protein", "b12", "vitamin_d"]',
        '"notable_nutrients": ["protein", "sodium"]',
    )
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=bad_json)
    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        resolved = await _estimate_llm("Milk", "Nandini", "500ml")
    assert "sodium" not in resolved["notable_nutrients"]
    assert "protein" in resolved["notable_nutrients"]


@pytest.mark.asyncio
async def test_estimate_llm_notable_nutrients_rejects_null_value_claim():
    """Model claims 'calcium' as notable but gave calcium_per_100g: null —
    must be stripped (never assert a nutrient with no value)."""
    bad_json = _LLM_FULL_JSON.replace(
        '"calcium_per_100g": 120,', '"calcium_per_100g": null,'
    ).replace(
        '"notable_nutrients": ["protein", "b12", "vitamin_d"]',
        '"notable_nutrients": ["protein", "calcium"]',
    )
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=bad_json)
    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        resolved = await _estimate_llm("Milk", "Nandini", "500ml")
    assert "calcium" not in resolved["notable_nutrients"]


@pytest.mark.asyncio
async def test_estimate_llm_notable_nutrients_rejects_below_floor_claim():
    """Model claims 'protein' as notable but the value it gave is below the
    mechanical floor — the floor backstop strips it even though the model's
    own number is non-null."""
    low_protein_json = _LLM_FULL_JSON.replace('"protein_per_100g": 3.2,', '"protein_per_100g": 0.5,')
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=low_protein_json)
    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        resolved = await _estimate_llm("Milk", "Nandini", "500ml")
    assert "protein" not in resolved["notable_nutrients"]


@pytest.mark.asyncio
async def test_estimate_llm_falls_back_to_mechanical_notable_nutrients_when_all_rejected():
    """If everything the model claimed fails validation, fall back to the
    mechanical floor-check over the model's own numeric values — never
    silently return an empty list when the numbers themselves qualify.

    _LLM_FULL_JSON values against _NOTABLE_FLOORS: calcium=120 (>=100 floor,
    notable) and vitamin_d=1.2 (>=1.0 floor, notable) are the only two that
    clear their floor; protein=3.2 (<5.0), iron=0.1 (<2.0), potassium=150
    (<200), vitamin_c=0 (<10), b12=0.4 (<0.5) all fall short."""
    bad_json = _LLM_FULL_JSON.replace(
        '"notable_nutrients": ["protein", "b12", "vitamin_d"]',
        '"notable_nutrients": ["sodium", "sugar"]',  # all disallowed
    )
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=bad_json)
    with patch("app.providers.factory.get_llm_provider", return_value=mock_llm):
        resolved = await _estimate_llm("Milk", "Nandini", "500ml")
    assert resolved["notable_nutrients"] == ["calcium", "vitamin_d"]
