"""
Nutrition API routes.

GET  /v1/nutrition/order/{order_id}   — per-order nutrition card
GET  /v1/nutrition/weekly             — weekly trends (trailing N weeks)
GET  /v1/nutrition/compliance         — diet compliance flags
PATCH /v1/nutrition/goals             — upsert household nutrition goals
"""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.schemas.common import APIResponse
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/nutrition", tags=["nutrition"])


def _get_household_id(request: Request) -> str | None:
    return request.session.get("household_id")


@router.get("/order/{order_id}", response_model=APIResponse)
async def get_order_nutrition(
    request: Request,
    order_id: str,
    db: AsyncSession = Depends(get_db),
):
    household_id = _get_household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    from app.models.db import Order, OrderNutrition

    # Verify order belongs to this household
    order_result = await db.execute(
        select(Order).where(Order.id == order_id, Order.household_id == household_id)
    )
    order = order_result.scalar_one_or_none()
    if not order:
        return APIResponse.fail("NOT_FOUND", "Order not found.")

    on_result = await db.execute(
        select(OrderNutrition).where(OrderNutrition.order_id == order_id)
    )
    on = on_result.scalar_one_or_none()
    if not on:
        # Task not yet complete — tell client to retry
        return JSONResponse(
            status_code=202,
            content={"success": True, "data": {"status": "computing", "retry_after": 10}},
        )

    return APIResponse.ok({
        "order_id":             order_id,
        "computed_at":          on.computed_at.isoformat(),
        "total_calories":       on.total_calories,
        "total_protein_g":      on.total_protein_g,
        "total_carbs_g":        on.total_carbs_g,
        "total_fat_g":          on.total_fat_g,
        "total_fiber_g":        on.total_fiber_g,
        "total_sodium_mg":      on.total_sodium_mg,
        "nutrient_totals":      on.nutrient_totals,
        "total_items":          on.total_items,
        "resolved_items":       on.resolved_items,
        "high_confidence_items": on.high_confidence_items,
        "llm_estimated_items":  on.llm_estimated_items,
        "unresolved_items":     on.unresolved_items,
        "item_breakdown":       on.item_breakdown,
    })


@router.get("/weekly", response_model=APIResponse)
async def get_weekly_nutrition(
    request: Request,
    weeks: int = Query(default=4, ge=1, le=12),
    db: AsyncSession = Depends(get_db),
):
    household_id = _get_household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    from app.models.db import Order, OrderNutrition, Household, HouseholdMember
    from app.utils.nutrition_targets import personalised_weekly_targets

    hh_result = await db.execute(select(Household).where(Household.id == household_id))
    hh = hh_result.scalar_one_or_none()
    if not hh:
        return APIResponse.fail("NOT_FOUND", "Household not found.")

    members = (await db.execute(
        select(HouseholdMember).where(HouseholdMember.household_id == household_id)
    )).scalars().all()
    weekly_targets = personalised_weekly_targets(members, hh.member_count or 1)

    cutoff = datetime.utcnow() - timedelta(weeks=weeks)
    result = await db.execute(
        select(
            func.date_trunc("week", Order.placed_at).label("week_start"),
            func.sum(OrderNutrition.total_calories).label("total_calories"),
            func.sum(OrderNutrition.total_protein_g).label("total_protein_g"),
            func.sum(OrderNutrition.total_carbs_g).label("total_carbs_g"),
            func.sum(OrderNutrition.total_fat_g).label("total_fat_g"),
            func.sum(OrderNutrition.total_fiber_g).label("total_fiber_g"),
            func.sum(OrderNutrition.total_sodium_mg).label("total_sodium_mg"),
            func.count(OrderNutrition.id).label("order_count"),
        )
        .join(Order, Order.id == OrderNutrition.order_id)
        .where(OrderNutrition.household_id == household_id)
        .where(Order.placed_at >= cutoff)
        .group_by(func.date_trunc("week", Order.placed_at))
        .order_by(func.date_trunc("week", Order.placed_at).desc())
    )
    rows = result.all()

    return APIResponse.ok({
        "weeks": [
            {
                "week_start":      row.week_start.isoformat() if row.week_start else None,
                "total_calories":  row.total_calories,
                "total_protein_g": row.total_protein_g,
                "total_carbs_g":   row.total_carbs_g,
                "total_fat_g":     row.total_fat_g,
                "total_fiber_g":   row.total_fiber_g,
                "total_sodium_mg": row.total_sodium_mg,
                "order_count":     row.order_count,
            }
            for row in rows
        ],
        "weekly_targets": weekly_targets,
    })


@router.get("/compliance", response_model=APIResponse)
async def get_compliance(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    household_id = _get_household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    import json
    from app.redis import get_redis
    redis = await get_redis()
    raw = await redis.get(f"compliance:{household_id}")
    flags = json.loads(raw) if raw else []
    return APIResponse.ok({"flags": flags})


@router.patch("/goals", response_model=APIResponse)
async def update_goals(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    household_id = _get_household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    body = await request.json()
    from app.models.db import HouseholdNutritionGoals

    result = await db.execute(
        select(HouseholdNutritionGoals).where(HouseholdNutritionGoals.household_id == household_id)
    )
    goals = result.scalar_one_or_none()
    if not goals:
        goals = HouseholdNutritionGoals(
            id=str(uuid.uuid4()),
            household_id=household_id,
        )
        db.add(goals)

    for field in ("daily_calories", "daily_protein_g", "daily_fiber_g", "daily_sodium_mg"):
        if field in body:
            setattr(goals, field, body[field])

    await db.commit()
    return APIResponse.ok({
        "daily_calories":  goals.daily_calories,
        "daily_protein_g": goals.daily_protein_g,
        "daily_fiber_g":   goals.daily_fiber_g,
        "daily_sodium_mg": goals.daily_sodium_mg,
    })


@router.get("/targets", response_model=APIResponse)
async def get_nutrition_targets(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Gap-to-Cart Phase A (targets-UX layer): per-member breakdown on top of
    personalised_weekly_targets. Computes nothing new — per_member_targets()
    and personalised_weekly_targets() are the same functions /weekly and
    /dashboard already call; household.weekly here is that exact call,
    never a re-derivation.

    Note: HouseholdMember has no `name` column (names aren't part of this
    data model — see onboarding). Per-member rows are identified by `role`
    instead of the illustrative "name" field in the PRD's example response.
    """
    household_id = _get_household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    from app.models.db import Household, HouseholdMember
    from app.utils.nutrition_targets import per_member_targets, personalised_weekly_targets

    hh_result = await db.execute(select(Household).where(Household.id == household_id))
    hh = hh_result.scalar_one_or_none()
    if not hh:
        return APIResponse.fail("NOT_FOUND", "Household not found.")

    members_result = await db.execute(
        select(HouseholdMember).where(HouseholdMember.household_id == household_id)
    )
    members = members_result.scalars().all()

    per_member = []
    any_fallback = False
    for m in members:
        # Mirrors the tier-1 condition in nutrition_targets.py's
        # _member_calories: full data present -> Mifflin-St Jeor path.
        # Anything less is some tier of fallback.
        fallback_used = not (m.age_years is not None and m.weight_kg is not None and m.height_cm is not None)
        any_fallback = any_fallback or fallback_used
        per_member.append({
            "member_id": m.id,
            "role": m.role,
            "age_years": m.age_years,
            "daily": per_member_targets(m),
            "fallback_used": fallback_used,
        })

    member_count = hh.member_count or 1
    if len(members) < member_count:
        any_fallback = True  # unmapped slots score as adult defaults too

    # Same function /weekly and /dashboard already call — called once, not
    # recomputed. This is the reconciliation contract the whole endpoint rests on.
    weekly = personalised_weekly_targets(members, member_count)
    daily = {
        "calories":  round(weekly["calories"] / 7),
        "protein_g": round(weekly["protein_g"] / 7, 1),
        "fiber_g":   round(weekly["fiber_g"] / 7, 1),
        "sodium_mg": round(weekly["sodium_mg"] / 7),
    }

    return APIResponse.ok({
        "source": "role_fallback" if any_fallback else "personalized",
        "per_member": per_member,
        "household": {"daily": daily, "weekly": weekly},
    })


@router.get("/gaps", response_model=APIResponse)
async def get_nutrition_gaps(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Gap-to-Cart Phase B3: this week's shortfall vs personalized targets,
    each backed by ranked, diet-safe, in-stock, live-priced recommendations.
    """
    household_id = _get_household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    from app.models.db import Household
    from app.services.nutrition_gaps import compute_gaps, get_recommendations_for_nutrient

    hh_result = await db.execute(select(Household).where(Household.id == household_id))
    household = hh_result.scalar_one_or_none()
    if not household:
        return APIResponse.fail("NOT_FOUND", "Household not found.")

    gaps = await compute_gaps(db, household)

    for gap in gaps:
        if gap["status"] == "short":
            try:
                gap["recommendations"] = await get_recommendations_for_nutrient(db, gap["nutrient"], household)
            except Exception as e:
                logger.warning("gap_recommendations_failed", nutrient=gap["nutrient"], error=str(e))
                gap["recommendations"] = []

    return APIResponse.ok({
        "gaps": gaps,
        "computed_at": datetime.utcnow().isoformat() + "Z",
    })
