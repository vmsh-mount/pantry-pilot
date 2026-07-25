"""
Integration tests — Gap-to-Cart Phase B3: gap detection & recommendation API
(app.services.nutrition_gaps, GET /v1/nutrition/gaps)

Covers the PRD's Definition of Done:
  1. Gap diff reads personalised_weekly_targets directly — no parallel
     target calculation exists in nutrition_gaps.py.
  2. A nutrient below 60% coverage returns insufficient_data, never a gap
     entry — a B12-only-OFF-resolved fixture (OFF never populates B12).
  3. Mock Swiggy MCP: protein gap -> dal/paneer recommended; an allergen
     item excluded; a non-vegetarian item excluded for a vegetarian household.
  4. per_rupee in the response is traceably computed from the live search
     price — never a stored value (no such column exists).
  5. Response omits on_track nutrients by default.
"""

import uuid
from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, patch

from tests.integration.conftest import create_household


async def _create_household_with_address(db, swiggy_user_id="swiggy_gaps_001",
                                            diet_type="vegetarian", allergies=None):
    from app.models.db import Address, HouseholdPreferences, Household
    from sqlalchemy import update, select

    household_id = await create_household(db, swiggy_user_id)

    await db.execute(
        update(Household).where(Household.id == household_id).values(
            diet_type=diet_type, allergies=allergies or [],
        )
    )

    addr = Address(household_id=household_id, swiggy_address_id="addr_home_001", label="Home", is_default=True)
    db.add(addr)
    await db.flush()
    await db.execute(
        update(HouseholdPreferences)
        .where(HouseholdPreferences.household_id == household_id)
        .values(preferred_address_id=addr.id)
    )
    await db.commit()

    hh = (await db.execute(select(Household).where(Household.id == household_id))).scalar_one()
    return hh


async def _add_order_nutrition(db, household_id: str, item_breakdown: list[dict], placed_at=None):
    from app.models.db import Order, OrderNutrition
    order = Order(
        id=str(uuid.uuid4()), household_id=household_id,
        swiggy_order_id=f"order_{uuid.uuid4().hex[:8]}", swiggy_address_id="addr_home_001",
        item_total=100, grand_total=100, placed_at=placed_at or datetime.now(timezone.utc),
    )
    db.add(order)
    await db.flush()

    resolved_count = sum(1 for i in item_breakdown if i.get("confidence") not in (None, "unresolved"))
    on = OrderNutrition(
        id=str(uuid.uuid4()), order_id=order.id, household_id=household_id,
        total_calories=sum(i.get("calories") or 0 for i in item_breakdown),
        total_protein_g=sum(i.get("protein_g") or 0 for i in item_breakdown),
        total_fiber_g=sum(i.get("fiber_g") or 0 for i in item_breakdown),
        total_carbs_g=0, total_fat_g=0, total_sodium_mg=0,
        total_items=len(item_breakdown), resolved_items=resolved_count,
        item_breakdown=item_breakdown,
    )
    db.add(on)
    await db.flush()
    await db.commit()
    return order, on


def _item(name, sku_id, confidence="high", calories=None, protein_g=None, fiber_g=None, nutrients=None):
    return {
        "item_name": name, "sku_id": sku_id, "source": "off", "confidence": confidence,
        "quantity_g": 200, "calories": calories, "protein_g": protein_g,
        "carbs_g": None, "fat_g": None, "fiber_g": fiber_g, "sodium_mg": None,
        "nutrients": nutrients or {},
    }


# ── 1. Target read directly, no parallel calculation ─────────────────────────

@pytest.mark.asyncio
async def test_gap_diff_reads_personalised_weekly_targets_directly(db):
    """compute_gaps must call the shared target function — patch it and
    confirm it's invoked, proving there's no second, independently-computed
    target hiding in nutrition_gaps.py."""
    from app.services.nutrition_gaps import compute_gaps

    hh = await _create_household_with_address(db)
    # At least one resolved item so coverage clears the guard — otherwise
    # coverage is 0/0 and every nutrient routes to insufficient_data before
    # target_weekly is ever populated, which would mask what this test checks.
    items = [_item("Milk", "SKU1", calories=60, protein_g=3, fiber_g=0, nutrients={})]
    await _add_order_nutrition(db, hh.id, items)

    with patch(
        "app.services.nutrition_gaps.personalised_weekly_targets",
        return_value={"calories": 99999, "protein_g": 99999, "fiber_g": 99999, "sodium_mg": 99999},
    ) as mock_targets:
        gaps = await compute_gaps(db, hh)

    mock_targets.assert_called_once()
    # With a deliberately huge target and zero actuals, protein/fiber/calories
    # must all show as short by (target - 0) == target — proving the patched
    # value is what's actually used, not a shadow calculation.
    protein_gap = next(g for g in gaps if g["nutrient"] == "protein")
    assert protein_gap["target_weekly"] == 99999


# ── 2. Coverage guard ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b12_low_coverage_returns_insufficient_data_not_gap(db):
    """All items resolved via OFF (which never populates b12) -> coverage=0
    for b12 -> insufficient_data, never a flagged deficiency."""
    from app.services.nutrition_gaps import compute_gaps

    hh = await _create_household_with_address(db, diet_type="vegetarian")
    items = [
        _item("Milk", "SKU1", calories=60, protein_g=6, fiber_g=0, nutrients={}),  # OFF: no b12 key at all
        _item("Curd", "SKU2", calories=60, protein_g=7, fiber_g=0, nutrients={}),
    ]
    await _add_order_nutrition(db, hh.id, items)

    gaps = await compute_gaps(db, hh)
    b12_gap = next((g for g in gaps if g["nutrient"] == "b12"), None)
    assert b12_gap is not None
    assert b12_gap["status"] == "insufficient_data"
    assert b12_gap["coverage"] == 0.0
    assert "short_by" not in b12_gap  # never emitted as a gap entry


@pytest.mark.asyncio
async def test_b12_adequate_coverage_and_zero_actual_triggers_watch_gap(db):
    """>= 60% of resolved items carry a (possibly zero) b12 value -> coverage
    guard passes; actual == 0 -> the watch-list trigger fires."""
    from app.services.nutrition_gaps import compute_gaps

    hh = await _create_household_with_address(db, diet_type="vegetarian")
    items = [
        _item("Fortified Cereal", "SKU1", calories=100, protein_g=3, fiber_g=2,
              nutrients={"vitamin_b12_g": 0.0}),  # LLM-resolved, value present but zero
        _item("Rice", "SKU2", calories=130, protein_g=2, fiber_g=0,
              nutrients={"vitamin_b12_g": 0.0}),
    ]
    await _add_order_nutrition(db, hh.id, items)

    gaps = await compute_gaps(db, hh)
    b12_gap = next(g for g in gaps if g["nutrient"] == "b12")
    assert b12_gap["status"] == "short"
    assert b12_gap["watch_reason"] == "no_source_in_window"
    assert b12_gap["target_weekly"] is None


# ── 3. Recommendation pipeline: guardrails + dal/paneer recommended ──────────

@pytest.mark.asyncio
async def test_protein_gap_recommends_dal_paneer_excludes_allergen_and_nonveg(db, swiggy_mcp):
    """Seeds protein candidates (dal, paneer). Mocks Swiggy search to return
    four products: a real dal item, a real paneer item, an allergen item
    (peanut — household is allergic), and a non-veg item (name contains
    'chicken', even though the searched concept was vegetarian) — simulating
    live search surfacing something the concept-level filter didn't
    anticipate. resolve_item is mocked to always fail resolution, forcing
    every recommendation through the concept-level estimate fallback —
    isolating the guardrail logic from live OFF/USDA/LLM network calls."""
    from app.models.db import NutrientFoodCandidate
    from app.services.nutrition_gaps import get_recommendations_for_nutrient

    hh = await _create_household_with_address(db, diet_type="vegetarian", allergies=["peanut"])

    for concept, per_100g, rr in [("dal", 22.0, 0.6), ("paneer", 18.0, 0.4)]:
        db.add(NutrientFoodCandidate(
            id=str(uuid.uuid4()), nutrient="protein", food_concept=concept,
            diet_tags=["vegetarian", "vegan", "jain"] if concept == "dal" else ["vegetarian", "jain"],
            nutrient_per_100g=per_100g, sample_size=10, confidence="medium",
            order_frequency=5, repurchase_rate=rr,
        ))
    await db.commit()

    from tests.integration.conftest import _mcp_ok
    swiggy_mcp["search_products"] = _mcp_ok("search_products", override={
        "products": [
            {"productId": "sku_dal", "displayName": "Toor Dal", "brand": "Tata Sampann",
             "inStock": True, "variations": [{
                 "spinId": "sku_dal", "displayName": "Toor Dal 1kg", "brandName": "Tata Sampann",
                 "quantityDescription": "1 kg", "isInStockAndAvailable": True,
                 "price": {"mrp": 180.0, "offerPrice": 180.0},
             }]},
            {"productId": "sku_paneer", "displayName": "Paneer", "brand": "Milky Mist",
             "inStock": True, "variations": [{
                 "spinId": "sku_paneer", "displayName": "Paneer 200g", "brandName": "Milky Mist",
                 "quantityDescription": "200 g", "isInStockAndAvailable": True,
                 "price": {"mrp": 90.0, "offerPrice": 90.0},
             }]},
            {"productId": "sku_peanut", "displayName": "Peanut Chikki", "brand": "Local",
             "inStock": True, "variations": [{
                 "spinId": "sku_peanut", "displayName": "Peanut Chikki 100g", "brandName": "Local",
                 "quantityDescription": "100 g", "isInStockAndAvailable": True,
                 "price": {"mrp": 40.0, "offerPrice": 40.0},
             }]},
            {"productId": "sku_chicken", "displayName": "Chicken Dal Makhani", "brand": "ReadyMeal",
             "inStock": True, "variations": [{
                 "spinId": "sku_chicken", "displayName": "Chicken Dal Makhani 300g", "brandName": "ReadyMeal",
                 "quantityDescription": "300 g", "isInStockAndAvailable": True,
                 "price": {"mrp": 150.0, "offerPrice": 150.0},
             }]},
        ],
        "totalCount": 4,
    })

    try:
        with patch("app.services.nutrition_gaps.resolve_item", new=AsyncMock(return_value={"confidence": "unresolved"})):
            results = await get_recommendations_for_nutrient(db, "protein", hh)
    finally:
        # swiggy_mcp is session-scoped — an override left in place here would
        # leak into every later test that hits search_products (this bit a
        # planning_graph test with a bogus response shape before this fix).
        del swiggy_mcp["search_products"]

    names = [r["item_name"] for r in results]
    assert any("Dal" in n or "Paneer" in n for n in names)
    assert not any("Peanut" in n for n in names), f"allergen leaked into recommendations: {names}"
    assert not any("Chicken" in n for n in names), f"non-veg leaked into recommendations: {names}"
    for r in results:
        assert r["confidence"] == "estimate"  # resolve_item was forced to fail


# ── 4. per_rupee traceability ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_per_rupee_computed_from_live_price_not_stored(db):
    """No nutrient_per_rupee column exists anywhere in this schema (Phase 0
    note) — per_rupee in the response must equal delivers/live_price exactly,
    computed in the request path, for whatever price the live search just
    returned.

    Patches BasketEditingService.search_items directly (query-aware) rather
    than the static SWIGGY_RESPONSES override: with only one learned
    candidate row the table is below the seed-fallback threshold, so the
    pipeline searches ~10 seed concepts too — a static mock would return the
    same product for all of them, producing duplicate entries that have
    nothing to do with what this test checks. A query-aware mock (return
    the dal product only for the 'dal' query, [] otherwise) keeps the
    result set to exactly the one product this test cares about, regardless
    of how many concepts get searched."""
    from app.mcp.types import MCPProduct
    from app.models.db import NutrientFoodCandidate
    from app.services.nutrition_gaps import get_recommendations_for_nutrient

    hh = await _create_household_with_address(db, diet_type="vegetarian")

    db.add(NutrientFoodCandidate(
        id=str(uuid.uuid4()), nutrient="protein", food_concept="dal",
        diet_tags=["vegetarian", "vegan", "jain"], nutrient_per_100g=20.0,
        sample_size=10, confidence="medium", order_frequency=5, repurchase_rate=0.5,
    ))
    await db.commit()

    live_price = 200.0
    dal_product = MCPProduct(
        sku_id="sku_dal", spin_id="sku_dal", name="Toor Dal 1kg", brand="Tata",
        quantity="1 kg", price=live_price, in_stock=True,
    )

    async def _search_side_effect(db, household_id, query, limit=3):
        return [dal_product] if query == "dal" else []

    with (
        patch("app.services.basket_editing_service.BasketEditingService.search_items",
              new=AsyncMock(side_effect=_search_side_effect)),
        patch("app.services.nutrition_gaps.resolve_item", new=AsyncMock(return_value={"confidence": "unresolved"})),
    ):
        results = await get_recommendations_for_nutrient(db, "protein", hh)

    assert len(results) == 1
    r = results[0]
    # Only 1 learned row exists (below the N=3 seed-fallback threshold), so
    # this resolves via the seed file's "dal" entry (24.0g/100g protein), not
    # the DB row's 20.0 — 24.0g/100g x 1000g pack = 240g delivered. The point
    # of this test is that per_rupee == delivers/live_price regardless of
    # which source produced the density value.
    assert r["unit_price"] == live_price
    assert r["delivers"] == pytest.approx(240.0, abs=0.5)
    assert r["per_rupee"] == pytest.approx(r["delivers"] / live_price, abs=0.001)


# ── 4b. Recommendation dedup — real bug found via manual testing ─────────────
# GET /v1/nutrition/gaps returned "Tata Sampann Unpolished Kala Chana" twice
# with the IDENTICAL sku_id, because two different candidate concepts (e.g.
# "chana" and "dal") both searched Swiggy and both happened to match the same
# product — nothing deduplicated across concept searches before ranking.

@pytest.mark.asyncio
async def test_recommendations_dedup_same_sku_across_concepts(db):
    """The same sku_id surfacing under two different candidate concept
    searches must appear only once in the final results."""
    from app.mcp.types import MCPProduct
    from app.models.db import NutrientFoodCandidate
    from app.services.nutrition_gaps import get_recommendations_for_nutrient

    hh = await _create_household_with_address(db, diet_type="vegetarian")

    for concept, per_100g in [("dal", 22.0), ("chana", 19.0), ("rajma", 23.0)]:
        db.add(NutrientFoodCandidate(
            id=str(uuid.uuid4()), nutrient="protein", food_concept=concept,
            diet_tags=["vegetarian", "vegan", "jain"], nutrient_per_100g=per_100g,
            sample_size=10, confidence="medium", order_frequency=5, repurchase_rate=0.5,
        ))
    await db.commit()

    # Same product (same sku_id) returned for BOTH "dal" and "chana" queries —
    # simulates Swiggy's fuzzy search overlap that produced the real bug.
    kala_chana = MCPProduct(
        sku_id="sku_kala_chana", spin_id="sku_kala_chana",
        name="Tata Sampann Unpolished Kala Chana", brand="Tata Sampann",
        quantity="500 g", price=58.0, in_stock=True,
    )
    rajma_product = MCPProduct(
        sku_id="sku_rajma", spin_id="sku_rajma", name="Rajma", brand="Fortune",
        quantity="500 g", price=70.0, in_stock=True,
    )

    async def _search_side_effect(db, household_id, query, limit=3):
        if query in ("dal", "chana"):
            return [kala_chana]
        if query == "rajma":
            return [rajma_product]
        return []

    with (
        patch("app.services.basket_editing_service.BasketEditingService.search_items",
              new=AsyncMock(side_effect=_search_side_effect)),
        patch("app.services.nutrition_gaps.resolve_item", new=AsyncMock(return_value={"confidence": "unresolved"})),
    ):
        results = await get_recommendations_for_nutrient(db, "protein", hh)

    sku_ids = [r["sku_id"] for r in results]
    assert len(sku_ids) == len(set(sku_ids)), f"duplicate sku_id in results: {sku_ids}"
    assert sku_ids.count("sku_kala_chana") == 1


@pytest.mark.asyncio
async def test_recommendations_prefer_variety_across_food_concepts(db):
    """When Swiggy returns multiple distinct SKUs for the SAME concept (e.g.
    two different soya chunks brands), only the best-scoring one should take
    a slot — favoring a spread of different foods over multiple near-
    duplicates of the same underlying food."""
    from app.mcp.types import MCPProduct
    from app.models.db import NutrientFoodCandidate
    from app.services.nutrition_gaps import get_recommendations_for_nutrient

    hh = await _create_household_with_address(db, diet_type="vegetarian")

    for concept, per_100g in [("soya", 52.0), ("dal", 22.0)]:
        db.add(NutrientFoodCandidate(
            id=str(uuid.uuid4()), nutrient="protein", food_concept=concept,
            diet_tags=["vegetarian", "vegan", "jain"], nutrient_per_100g=per_100g,
            sample_size=10, confidence="medium", order_frequency=5, repurchase_rate=0.5,
        ))
    await db.commit()

    # Two DIFFERENT SKUs, both under the "soya" concept — two brands, same food.
    soya_a = MCPProduct(sku_id="sku_soya_a", spin_id="sku_soya_a",
                         name="Supreme Harvest Soya Chunks", brand="Supreme Harvest",
                         quantity="200 g", price=30.0, in_stock=True)
    soya_b = MCPProduct(sku_id="sku_soya_b", spin_id="sku_soya_b",
                         name="Fortune Soya Chunks", brand="Fortune",
                         quantity="200 g", price=25.0, in_stock=True)  # cheaper -> higher per_rupee
    dal_product = MCPProduct(sku_id="sku_dal", spin_id="sku_dal", name="Toor Dal",
                              brand="Tata", quantity="1 kg", price=180.0, in_stock=True)

    async def _search_side_effect(db, household_id, query, limit=3):
        if query == "soya":
            return [soya_a, soya_b]
        if query == "dal":
            return [dal_product]
        return []

    with (
        patch("app.services.basket_editing_service.BasketEditingService.search_items",
              new=AsyncMock(side_effect=_search_side_effect)),
        patch("app.services.nutrition_gaps.resolve_item", new=AsyncMock(return_value={"confidence": "unresolved"})),
    ):
        results = await get_recommendations_for_nutrient(db, "protein", hh)

    soya_entries = [r for r in results if r["sku_id"] in ("sku_soya_a", "sku_soya_b")]
    assert len(soya_entries) == 1, f"expected exactly one soya entry (variety), got: {soya_entries}"
    # The cheaper soya SKU (higher protein-per-rupee) should be the one kept.
    assert soya_entries[0]["sku_id"] == "sku_soya_b"
    assert any(r["sku_id"] == "sku_dal" for r in results)


# ── 5. on_track nutrients omitted by default ──────────────────────────────────

@pytest.mark.asyncio
async def test_on_track_nutrients_omitted_from_response(db):
    """Actuals meeting or exceeding target for all tracked nutrients ->
    empty gaps list (no on_track entries emitted)."""
    from app.services.nutrition_gaps import compute_gaps

    hh = await _create_household_with_address(db, diet_type=None)  # no diet watch-list triggered
    with patch(
        "app.services.nutrition_gaps.personalised_weekly_targets",
        return_value={"calories": 100, "protein_g": 10, "fiber_g": 5, "sodium_mg": 2300},
    ):
        items = [_item("Big Meal", "SKU1", calories=200, protein_g=20, fiber_g=10, nutrients={})]
        await _add_order_nutrition(db, hh.id, items)
        gaps = await compute_gaps(db, hh)

    assert gaps == []
