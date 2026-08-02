"""
Unit tests — quantity parsing (tasks/features/nutrition-quantity-parsing.md)

No existing coverage for _parse_quantity_g existed before this PRD.

Covers:
  1. The two proven-broken demo-catalog cases (eggs "6 pcs", banana
     "1 dozen") now resolve correctly.
  2. Word-boundary + plural correctness — the case the PRD's review round
     caught: raw substring matching would let "egg" match inside
     "Eggless", attributing egg weight to an egg-free product.
  3. Multipack detection and the guard against silently misparsing an
     unrecognized multipack shape.
  4. Unknown-food-noun counts stay unresolvable rather than guessing.
  5. Regression: existing weight/volume parsing is unaffected.
  6. _parse_count in isolation.
"""

import pytest

from app.services.nutrition_resolution import _parse_quantity_g, _parse_count, _parse_weight_or_volume


# ── 1. Proven-broken demo-catalog cases ──────────────────────────────────────

def test_eggs_pcs_resolves():
    # demo_catalog.py: ("Farm Fresh Eggs", ..., "6 pcs", ...) — was None before this fix.
    assert _parse_quantity_g("6.0 pcs", "Farm Fresh Eggs") == 6 * 55.0


def test_banana_dozen_resolves():
    # demo_catalog.py: ("Banana", ..., "1 dozen", ...) — was None before this fix.
    assert _parse_quantity_g("1.0 dozen", "Banana") == 12 * 120.0


def test_dozen_multiplier():
    assert _parse_quantity_g("2 dozen", "Banana") == 24 * 120.0


# ── 2. Word-boundary + plural correctness ────────────────────────────────────

def test_eggless_product_stays_unresolvable():
    # The bug caught in review: raw substring "egg" in "eggless" would
    # wrongly attribute egg weight to an egg-free product.
    assert _parse_quantity_g("1 pcs", "Eggless Mayonnaise") is None
    assert _parse_quantity_g("6 pcs", "Eggless Chocolate Cake") is None


def test_plural_food_names_still_match():
    assert _parse_quantity_g("6 pcs", "Farm Fresh Eggs") == 6 * 55.0
    assert _parse_quantity_g("4 pcs", "Fresh Bananas") == 4 * 120.0
    assert _parse_quantity_g("3 pcs", "Red Apples") == 3 * 180.0


def test_irregular_plurals_resolve_via_es_suffix():
    assert _parse_quantity_g("4 pcs", "Baby Potatoes") == 4 * 150.0
    assert _parse_quantity_g("6 pcs", "Roma Tomatoes") == 6 * 100.0
    assert _parse_quantity_g("10 pcs", "Fresh Green Chillies") == 10 * 10.0


def test_lemongrass_does_not_collide_with_lemon():
    assert _parse_quantity_g("1 pcs", "Lemongrass Tea") is None


# ── 3. Multipack detection + guard ───────────────────────────────────────────

@pytest.mark.parametrize("qty_desc", ["2 x 200 g", "2x200g", "2 X 200g", "2x 200 g"])
def test_multipack_weight_computes_true_total(qty_desc):
    assert _parse_quantity_g(qty_desc, "Yakult") == 400.0


def test_multipack_kg():
    assert _parse_quantity_g("3x1kg", "Rice") == 3000.0


def test_multipack_volume_applies_density():
    # "2 x 500 ml" milk -> 1000ml total, then density-adjusted like any
    # other volume parse (milk density 1.03).
    assert _parse_quantity_g("2 x 500 ml", "Amul Toned Milk") == 1000.0 * 1.03


def test_unrecognized_multipack_shape_stays_unresolvable_not_misparsed():
    # Multipacks of countable items are explicitly out of scope — must fail
    # safe (None), not silently parse as "6" while ignoring the "2 x".
    assert _parse_quantity_g("2 x 6 pcs", "Kellogg's Muesli") is None


def test_plain_weight_not_treated_as_multipack():
    # Regression: no "x" in the string at all — must still work exactly as
    # before this change.
    assert _parse_quantity_g("200g", "Amul Malai Paneer") == 200.0


# ── 4. Unknown food noun with known count ────────────────────────────────────

def test_known_count_unknown_food_stays_unresolvable():
    assert _parse_quantity_g("6 pcs", "Britannia Marie Gold Biscuits") is None


# ── 5. Regression: existing weight/volume parsing unaffected ────────────────

def test_weight_kg():
    assert _parse_quantity_g("5 kg", "India Gate Basmati Rice") == 5000.0


def test_weight_g():
    assert _parse_quantity_g("500 g", "Amul Malai Paneer") == 500.0


def test_volume_liter_applies_density():
    assert _parse_quantity_g("1 L", "Amul Toned Milk") == 1000.0 * 1.03


def test_volume_ml_applies_density():
    assert _parse_quantity_g("500 ml", "Fortune Sunflower Oil") == 500.0 * 0.92


def test_empty_qty_desc_is_unresolvable():
    assert _parse_quantity_g("", "Anything") is None


def test_unparseable_string_is_unresolvable():
    assert _parse_quantity_g("assorted", "Gift Box") is None


# ── 6. _parse_count in isolation ─────────────────────────────────────────────

@pytest.mark.parametrize("qty_desc,expected", [
    ("1 dozen", 12.0),
    ("2 dozen", 24.0),
    ("6 pcs", 6.0),
    ("6 pc", 6.0),
    ("1 piece", 1.0),
    ("3 pieces", 3.0),
    ("1 unit", 1.0),
    ("4 units", 4.0),
    ("2 nos", 2.0),
    ("2 no", 2.0),
])
def test_parse_count_recognizes_formats(qty_desc, expected):
    assert _parse_count(qty_desc.lower()) == expected


def test_parse_count_returns_none_for_non_matching_string():
    assert _parse_count("5 kg") is None
    assert _parse_count("assorted") is None


# ── _parse_weight_or_volume in isolation (extraction sanity check) ─────────

def test_parse_weight_or_volume_matches_original_behavior():
    assert _parse_weight_or_volume("5 kg", "Rice") == 5000.0
    assert _parse_weight_or_volume("500 g", "Paneer") == 500.0
    assert _parse_weight_or_volume("1 l", "Milk") == 1000.0 * 1.03
    assert _parse_weight_or_volume("6 pcs", "Eggs") is None
