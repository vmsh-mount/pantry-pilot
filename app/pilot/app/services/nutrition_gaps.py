"""
Gap-to-Cart Phase B3: weekly gap diff, coverage guard, recommendation pipeline.

The join this feature exists for: weekly actual -> personalized target ->
gap -> ranked, diet-safe, in-stock SKUs -> basket-ready. See
tasks/features/nutrition-gap-to-cart-phase-b3-gap-detection.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Order, OrderNutrition, HouseholdMember, Household
from app.services.nutrition_resolution import NUTRIENT_KEYS, _density_value, _parse_quantity_g, resolve_item
from app.services.nutrient_candidates import get_candidates
from app.utils.nutrition_targets import personalised_weekly_targets
from app.utils.logging import get_logger

logger = get_logger(__name__)

# "Only emit a gap/deficiency for a nutrient when coverage >= 0.6" — PRD's
# coverage guard. Health-adjacent; not a tunable a caller can override.
COVERAGE_THRESHOLD = 0.6

_WEEK_LOOKBACK_DAYS = 7

# Diet-specific watch list: no numeric weekly target exists for these today,
# so "zero source in the trailing window" (once coverage-gated) is itself
# the trigger, not a target-vs-actual diff.
_WATCH_NUTRIENTS = ("b12", "iron")
_WATCH_DIETS = {"vegetarian", "vegan", "jain"}

_UNITS = {"calories": "kcal", "protein": "g", "fiber": "g", "iron": "mg", "b12": "mcg"}

# Recommendation pipeline sizing (PRD: "top ~8 candidate concepts", "top ~5 ... resolve").
_CANDIDATE_CONCEPTS = 8
_RECOMMENDATIONS_PER_GAP = 5


# ── Item-level actuals + coverage ─────────────────────────────────────────────

def _item_total_value(item: dict, nutrient_key: str) -> float | None:
    """Read a nutrient's absolute total from one item_breakdown entry.
    calories/protein/fiber/sodium are top-level keys on the entry (see
    tasks/nutrition.py's resolve_order_nutrition); everything else lives in
    the entry's nested 'nutrients' dict, keyed by NUTRIENT_KEYS[...]['total']."""
    if nutrient_key == "calories":
        return item.get("calories")
    total_field = NUTRIENT_KEYS[nutrient_key]["total"]
    if total_field in item:
        return item.get(total_field)
    return (item.get("nutrients") or {}).get(total_field)


async def _fetch_week_item_breakdown(db: AsyncSession, household_id: str) -> list[dict]:
    """
    All item_breakdown entries from OrderNutrition rows for orders placed in
    the trailing 7 days.

    Deliberately NOT a reuse of get_weekly_nutrition's SQL aggregation: that
    query returns pre-summed week-level totals with no item-level detail, and
    the coverage guard needs "what fraction of this week's resolved items had
    a value for nutrient X" — a question the aggregate query structurally
    cannot answer. This fetches the same OrderNutrition rows (same household
    filter, same "this week" semantics) but as ORM objects, then both the
    tracked-nutrient totals AND the coverage/micronutrient logic are derived
    from one pass over item_breakdown, so they can never disagree with each
    other the way two independently-aggregated queries could.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=_WEEK_LOOKBACK_DAYS)
    result = await db.execute(
        select(OrderNutrition)
        .join(Order, Order.id == OrderNutrition.order_id)
        .where(OrderNutrition.household_id == household_id)
        .where(Order.placed_at >= cutoff)
    )
    rows = result.scalars().all()
    items: list[dict] = []
    for row in rows:
        items.extend(row.item_breakdown or [])
    return items


def _weekly_actual_and_coverage(items: list[dict], nutrient_key: str) -> tuple[float, float]:
    """(actual weekly total, coverage fraction) for one nutrient, over the
    week's RESOLVED items only — an unresolved item was never a candidate to
    carry this nutrient's data, so it shouldn't dilute the coverage denominator."""
    resolved_items = [i for i in items if i.get("confidence") not in (None, "unresolved")]
    if not resolved_items:
        return 0.0, 0.0
    values = [_item_total_value(i, nutrient_key) for i in resolved_items]
    non_null = [v for v in values if v is not None]
    coverage = len(non_null) / len(resolved_items)
    actual = sum(non_null)
    return actual, coverage


# ── Gap diff ───────────────────────────────────────────────────────────────────

async def compute_gaps(db: AsyncSession, household: Household) -> list[dict]:
    """
    Diff this week's actuals against personalised_weekly_targets (read
    directly — no parallel target calculation lives in this file). Returns
    gap dicts in the API response shape, WITHOUT recommendations attached
    (that's get_recommendations_for_nutrient, called separately per gap by
    the API route — keeps this function testable without a live Swiggy
    dependency). Ranked worst-relative-gap first for target-based nutrients,
    then diet-watch-triggered nutrients (which have no target to rank by).
    on_track nutrients are omitted entirely, per the API spec.
    """
    members_result = await db.execute(
        select(HouseholdMember).where(HouseholdMember.household_id == household.id)
    )
    members = members_result.scalars().all()
    targets = personalised_weekly_targets(members, household.member_count or 1)

    items = await _fetch_week_item_breakdown(db, household.id)

    gaps: list[dict] = []

    # ── Target-based: calories, protein, fiber (sodium is a ceiling — see
    # the companion PRD — never a "shortfall" to recommend against). ──
    target_map = {
        "calories": ("calories", "kcal"),
        "protein": ("protein_g", "g"),
        "fiber": ("fiber_g", "g"),
    }
    ranked: list[dict] = []
    for nutrient, (target_key, unit) in target_map.items():
        actual, coverage = _weekly_actual_and_coverage(items, nutrient)
        if coverage < COVERAGE_THRESHOLD:
            gaps.append({"nutrient": nutrient, "status": "insufficient_data", "coverage": round(coverage, 2)})
            continue
        target = targets[target_key]
        short_by = target - actual
        if short_by <= 0:
            continue  # on_track, omitted by default
        ranked.append({
            "nutrient": nutrient, "status": "short",
            "target_weekly": target, "actual_weekly": round(actual, 1),
            "short_by": round(short_by, 1), "unit": unit,
            "_rel_gap": (short_by / target) if target else 0,
        })
    ranked.sort(key=lambda g: -g["_rel_gap"])
    for g in ranked:
        del g["_rel_gap"]
    gaps.extend(ranked)

    # ── Diet-specific watch list: b12, iron — no numeric target exists, so
    # "zero source in the trailing window" (coverage-gated) is the trigger. ──
    if household.diet_type in _WATCH_DIETS:
        for nutrient in _WATCH_NUTRIENTS:
            actual, coverage = _weekly_actual_and_coverage(items, nutrient)
            if coverage < COVERAGE_THRESHOLD:
                gaps.append({"nutrient": nutrient, "status": "insufficient_data", "coverage": round(coverage, 2)})
                continue
            if actual <= 0:
                gaps.append({
                    "nutrient": nutrient, "status": "short",
                    "target_weekly": None, "actual_weekly": round(actual, 1),
                    "short_by": None, "unit": _UNITS[nutrient],
                    "watch_reason": "no_source_in_window",
                })
            # else: on_track, omitted

    return gaps


# ── Recommendation pipeline ───────────────────────────────────────────────────

async def get_recommendations_for_nutrient(
    db: AsyncSession,
    nutrient: str,
    household: Household,
    limit: int = _RECOMMENDATIONS_PER_GAP,
) -> list[dict]:
    """
    Steps per the PRD:
      1. candidates from nutrient_food_candidate (B2, seed-fallback aware),
         filtered by diet_type/allergies, pre-ranked by repurchase_rate x
         nutrient_per_100g (both price-independent).
      2. live Swiggy search per candidate concept (top _CANDIDATE_CONCEPTS),
         in-stock only (BasketEditingService.search_items already filters).
      3. live per_rupee from the CONCEPT-level estimate x live price —
         never a stored value (Phase 0 schema note; no price field exists
         on nutrient_food_candidate).
      4. resolve top ~5 by live per-rupee through the real OFF/USDA/Haiku
         chain (bounded; mostly cache hits once B1/B2 have run a while).
      5. final re-rank by the freshly-resolved per_rupee.
      6. guardrails: hard-exclude allergens; diet safety net re-checked here
         even though step 1 already filtered by diet (defense in depth against
         a live search result the concept filter didn't anticipate). If
         resolution fails for an item, keep it at the concept-level estimate,
         confidence="estimate" — never drop it silently.
    """
    from app.services.basket_editing_service import BasketEditingService

    allergies = [a.lower() for a in (household.allergies or [])]
    diet_type = household.diet_type

    candidates, _used_seed = await get_candidates(db, nutrient, diet_type, limit=_CANDIDATE_CONCEPTS)
    if not candidates:
        return []

    svc = BasketEditingService()
    scored: list[dict] = []
    seen_sku_ids: set[str] = set()

    for cand in candidates:
        try:
            products = await svc.search_items(db, household.id, cand["food_concept"], limit=3)
        except Exception as e:
            logger.warning("gap_recommendation_search_failed", concept=cand["food_concept"], error=str(e))
            continue

        for p in products:
            # Different candidate concepts can surface the SAME Swiggy SKU
            # (fuzzy search overlap, e.g. a "chana" query and a "dal" query
            # both matching "Kala Chana") — without this, the exact same
            # sku_id could appear twice in the final recommendations.
            if p.sku_id in seen_sku_ids:
                continue

            name_lc = (p.name or "").lower()
            if any(a in name_lc for a in allergies):
                continue  # guardrail: hard-exclude allergens

            pack_g = _parse_quantity_g(p.quantity or "", cand["food_concept"])
            if not pack_g or not p.price or not cand.get("nutrient_per_100g"):
                continue
            delivers = cand["nutrient_per_100g"] * pack_g / 100
            per_rupee = delivers / p.price
            seen_sku_ids.add(p.sku_id)
            scored.append({"candidate": cand, "product": p, "per_rupee": per_rupee, "delivers": delivers})

    # Step 3 done — rank by the concept-level live per_rupee. Keep only the
    # single best-scoring item per food_concept before taking the top N, so
    # the final recommendations favor variety across different foods rather
    # than letting one concept (e.g. two different soya chunks brands) fill
    # multiple slots that could otherwise show the household a wider choice.
    scored.sort(key=lambda s: -s["per_rupee"])
    best_per_concept: dict[str, dict] = {}
    for s in scored:
        concept = s["candidate"]["food_concept"]
        if concept not in best_per_concept:
            best_per_concept[concept] = s  # first hit per concept = highest per_rupee, since scored is sorted
    to_resolve = sorted(best_per_concept.values(), key=lambda s: -s["per_rupee"])[:limit]

    results: list[dict] = []
    for s in to_resolve:
        p = s["product"]
        qty_desc = p.quantity or ""
        resolved = None
        try:
            resolved = await resolve_item(db, p.sku_id, p.name, p.brand, qty_desc)
        except Exception as e:
            logger.warning("gap_recommendation_resolve_failed", sku_id=p.sku_id, error=str(e))

        entry = None
        if resolved and resolved.get("confidence") not in (None, "unresolved"):
            density = _density_value(resolved, nutrient)
            pack_g = resolved.get("quantity_g") or _parse_quantity_g(qty_desc, s["candidate"]["food_concept"])
            if density is not None and pack_g and p.price:
                delivers = density * pack_g / 100
                entry = {
                    "sku_id": p.sku_id, "item_name": p.name, "brand": p.brand,
                    "unit_price": p.price, "delivers": round(delivers, 1),
                    "per_rupee": round(delivers / p.price, 3),
                    "confidence": resolved.get("confidence"),
                    "repurchase_rate": s["candidate"].get("repurchase_rate"),
                    "in_stock": p.in_stock,
                }

        if entry is None:
            # Step 6: resolution failed or gave nothing usable — keep the
            # concept-level estimate rather than dropping the item silently.
            entry = {
                "sku_id": p.sku_id, "item_name": p.name, "brand": p.brand,
                "unit_price": p.price, "delivers": round(s["delivers"], 1),
                "per_rupee": round(s["per_rupee"], 3), "confidence": "estimate",
                "repurchase_rate": s["candidate"].get("repurchase_rate"),
                "in_stock": p.in_stock,
            }

        results.append(entry)

    # Step 5: final re-rank by the just-computed per_rupee (resolved values
    # may differ from the concept-level estimate that produced the initial order).
    results.sort(key=lambda r: -r["per_rupee"])

    # Step 6 diet safety net — re-checked even though step 1 already filtered
    # by diet_type, in case live search surfaced something the concept-level
    # filter didn't anticipate (e.g. a mixed product name).
    if diet_type in ("vegetarian", "vegan", "jain"):
        from app.agent.planning_graph import _NON_VEG_KEYWORDS
        results = [r for r in results if not any(kw in r["item_name"].lower() for kw in _NON_VEG_KEYWORDS)]

    return results[:limit]
