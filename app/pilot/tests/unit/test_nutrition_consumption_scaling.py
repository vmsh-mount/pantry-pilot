"""
Unit tests — nutrition consumption scaling
(tasks/features/nutrition-consumed-not-purchased.md)

Covers:
  1. estimate_consumed_g: no learned rate -> full pack; rate below pack ->
     capped; rate above pack -> still capped at pack size; unparseable unit
     -> falls back to full pack.
  2. compute_item_totals: consumed_g=None preserves today's full-pack
     behavior; a supplied consumed_g scales totals by the capped quantity,
     not the pack; the function itself re-caps even if a caller passes an
     consumed_g larger than the pack.
"""

from app.services.nutrition_resolution import compute_item_totals, estimate_consumed_g


# ── 1. estimate_consumed_g ───────────────────────────────────────────────────

def test_no_pantry_rate_returns_full_pack():
    assert estimate_consumed_g(
        quantity_g=5000, avg_weekly_consumption=None, consumption_unit=None,
        item_name="India Gate Basmati Rice 5kg",
    ) == 5000


def test_no_consumption_unit_returns_full_pack():
    assert estimate_consumed_g(
        quantity_g=5000, avg_weekly_consumption=1.2, consumption_unit=None,
        item_name="India Gate Basmati Rice 5kg",
    ) == 5000


def test_rate_below_pack_is_capped():
    # 1.2 kg/week learned rate, 5kg pack -> capped at 1200g
    assert estimate_consumed_g(
        quantity_g=5000, avg_weekly_consumption=1.2, consumption_unit="kg",
        item_name="India Gate Basmati Rice 5kg",
    ) == 1200


def test_rate_above_pack_is_still_capped_at_pack_size():
    # A 200g paneer pack with a learned rate that implies faster consumption
    # than one pack holds -> can't exceed what was actually purchased.
    assert estimate_consumed_g(
        quantity_g=200, avg_weekly_consumption=0.5, consumption_unit="kg",
        item_name="Amul Malai Paneer 200g",
    ) == 200


def test_unparseable_unit_falls_back_to_full_pack():
    assert estimate_consumed_g(
        quantity_g=5000, avg_weekly_consumption=1.2, consumption_unit="xyz",
        item_name="India Gate Basmati Rice 5kg",
    ) == 5000


def test_zero_rate_returns_full_pack():
    # 0 is falsy — treated the same as "no rate learned yet", not "consumes none".
    assert estimate_consumed_g(
        quantity_g=5000, avg_weekly_consumption=0, consumption_unit="kg",
        item_name="India Gate Basmati Rice 5kg",
    ) == 5000


# ── 2. compute_item_totals ───────────────────────────────────────────────────

_RESOLVED = {
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


def test_no_consumed_g_scales_by_full_pack_today_behavior():
    out = compute_item_totals(_RESOLVED)
    assert out["calories"] == 350 * 50  # 5000g / 100 = 50
    assert out["pack_quantity_g"] == 5000
    assert out["consumed_g"] == 5000


def test_consumed_g_scales_by_capped_quantity_not_pack():
    out = compute_item_totals(_RESOLVED, consumed_g=1200)
    assert out["calories"] == 350 * 12  # 1200g / 100 = 12
    assert out["pack_quantity_g"] == 5000
    assert out["consumed_g"] == 1200


def test_consumed_g_larger_than_pack_is_still_capped_by_the_function():
    # Defensive: even if a caller forgets to pre-cap, compute_item_totals
    # itself never scales beyond the purchased pack.
    out = compute_item_totals(_RESOLVED, consumed_g=9000)
    assert out["calories"] == 350 * 50
    assert out["consumed_g"] == 5000


def test_unresolvable_quantity_returns_none_regardless_of_consumed_g():
    resolved = {**_RESOLVED, "quantity_unresolvable": True}
    out = compute_item_totals(resolved, consumed_g=1200)
    assert out["calories"] is None
    assert "pack_quantity_g" not in out
