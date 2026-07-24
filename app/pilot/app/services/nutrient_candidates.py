"""
Gap-to-Cart Phase B2/B3: query helper over nutrient_food_candidate, with the
cold-start seed-floor fallback rule.

The learned table (nutrient_food_candidate, populated nightly by
app.tasks.nutrition.rebuild_nutrient_food_map) is the primary source. The
seed floor (app/data/seed_nutrient_foods.json) is a one-time bootstrap that
is NEVER hand-edited after ship and NEVER synced into the table — it's
consulted only at query time, only for a (nutrient, diet) pair that doesn't
yet have enough learned data, and it silently stops mattering once real
usage accrues (see Phase B2 PRD).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import NutrientFoodCandidate
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Thresholds for "the learned table has enough coverage for this (nutrient, diet)."
# Suggested by the PRD; not sacred — revisit once real usage data exists.
SEED_FALLBACK_MIN_ROWS = 3    # N
SEED_FALLBACK_MIN_SAMPLE_SIZE = 5  # M

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_nutrient_foods.json"


@lru_cache(maxsize=1)
def _load_seed() -> dict[str, list[dict]]:
    with open(_SEED_PATH) as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _diet_matches(candidate_tags: list[str], diet_type: str | None) -> bool:
    """A candidate is usable for a household's diet_type if its tags cover it.
    No diet_type / unknown diet_type -> no dietary restriction, anything matches."""
    if not diet_type or diet_type not in ("vegetarian", "vegan", "jain"):
        return True
    return diet_type in (candidate_tags or [])


async def get_candidates(
    db: AsyncSession,
    nutrient: str,
    diet_type: str | None,
    limit: int = 10,
) -> tuple[list[dict], bool]:
    """
    Return (candidates, used_seed_fallback) for a nutrient, filtered by diet.

    Candidates are ranked by repurchase_rate desc (nulls last), then
    nutrient_per_100g desc — price-independent signals only; B3 computes
    nutrient_per_rupee at request time from a live price and re-ranks.

    used_seed_fallback is True when the learned table didn't have enough
    rows for this (nutrient, diet) pair and the seed floor was used instead
    — log/observe this per B3's coverage-guard and B2's DoD ("rows_from_seed_
    fallback... visible signal that the seed is shrinking in relevance").
    """
    result = await db.execute(
        select(NutrientFoodCandidate).where(NutrientFoodCandidate.nutrient == nutrient)
    )
    all_rows = result.scalars().all()
    matching = [r for r in all_rows if _diet_matches(r.diet_tags, diet_type)]
    adequate = [r for r in matching if (r.sample_size or 0) >= SEED_FALLBACK_MIN_SAMPLE_SIZE]

    if len(adequate) >= SEED_FALLBACK_MIN_ROWS:
        ranked = sorted(
            adequate,
            key=lambda r: (r.repurchase_rate is None, -(r.repurchase_rate or 0), -(r.nutrient_per_100g or 0)),
        )
        return (
            [
                {
                    "food_concept": r.food_concept,
                    "nutrient_per_100g": r.nutrient_per_100g,
                    "representative_sku_id": r.representative_sku_id,
                    "repurchase_rate": r.repurchase_rate,
                    "order_frequency": r.order_frequency,
                    "confidence": r.confidence,
                    "sample_size": r.sample_size,
                }
                for r in ranked[:limit]
            ],
            False,
        )

    # Seed fallback — this (nutrient, diet) pair doesn't have enough learned data yet.
    seed = _load_seed().get(nutrient, [])
    seed_matching = [s for s in seed if _diet_matches(s.get("diet_tags"), diet_type)]
    seed_matching.sort(key=lambda s: -(s.get("nutrient_per_100g") or 0))
    logger.info(
        "nutrient_candidates_seed_fallback_used",
        nutrient=nutrient, diet_type=diet_type,
        learned_adequate_rows=len(adequate), seed_rows=len(seed_matching),
    )
    return (
        [
            {
                "food_concept": s["food_concept"],
                "nutrient_per_100g": s.get("nutrient_per_100g"),
                "representative_sku_id": None,
                "repurchase_rate": None,
                "order_frequency": 0,
                "confidence": "estimate",
                "sample_size": 0,
            }
            for s in seed_matching[:limit]
        ],
        True,
    )
