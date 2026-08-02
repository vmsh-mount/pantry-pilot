"""
Dashboard API — single consolidated endpoint for the home screen.

GET /v1/dashboard  — returns flow status, routines summary, current-week stats,
                     nutrition macros, and recent orders in one response.

Time windows (all IST / Asia/Kolkata):
  - week.*       : current calendar week, Mon 00:00 → Sun 23:59
  - stats.*      : all-time counts from the orders table
  - recent_orders: last 3 placed orders (rolling)
"""

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.database import get_db
from app.schemas.common import APIResponse
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

IST = ZoneInfo("Asia/Kolkata")

PLANNING_STATES = {"pending", "sensing", "planning", "optimizing"}
PLACING_STATES  = {"confirmed", "placing"}


def _current_week_bounds() -> tuple[datetime, datetime]:
    """Return (week_start, next_monday) as UTC datetimes for half-open interval [start, next_monday)."""
    now_ist = datetime.now(IST)
    days_since_monday = now_ist.weekday()  # 0 = Monday
    monday      = now_ist.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_since_monday)
    next_monday = monday + timedelta(days=7)
    return monday.astimezone(timezone.utc), next_monday.astimezone(timezone.utc)


@router.get("", response_model=APIResponse)
async def get_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.db import (
        LoopRun, HouseholdPreferences, Household, HouseholdMember,
        Routine, Order, OrderItem, OrderNutrition, HouseholdNutritionGoals,
    )
    from app.utils.nutrition_targets import personalised_weekly_targets

    household_id = request.session.get("household_id")
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    # ── Household ──────────────────────────────────────────────────────────────
    hh_result = await db.execute(select(Household).where(Household.id == household_id))
    hh = hh_result.scalar_one_or_none()
    if not hh:
        return APIResponse.fail("NOT_FOUND", "Household not found.")

    # ── Flow status ────────────────────────────────────────────────────────────
    basket_run = (await db.execute(
        select(LoopRun)
        .where(LoopRun.household_id == household_id, LoopRun.state == "awaiting_confirmation")
        .order_by(desc(LoopRun.triggered_at))
        .limit(1)
    )).scalar_one_or_none()

    planning_run = None
    placing_run  = None
    if not basket_run:
        active_run = (await db.execute(
            select(LoopRun)
            .where(LoopRun.household_id == household_id, LoopRun.state.in_(PLANNING_STATES | PLACING_STATES))
            .order_by(desc(LoopRun.triggered_at))
            .limit(1)
        )).scalar_one_or_none()
        if active_run:
            if active_run.state in PLACING_STATES:
                placing_run = active_run
            else:
                planning_run = active_run

    prefs = (await db.execute(
        select(HouseholdPreferences).where(HouseholdPreferences.household_id == household_id)
    )).scalar_one_or_none()

    next_run_at = prefs.next_run_at.isoformat() if prefs and prefs.next_run_at else None

    flow = {
        "basket_pending": basket_run is not None,
        "in_progress":    planning_run is not None,
        "placing_order":  placing_run is not None,
        "next_run_at":    next_run_at,
    }

    # ── Routines ───────────────────────────────────────────────────────────────
    active_routines = (await db.execute(
        select(Routine)
        .where(Routine.household_id == household_id, Routine.status == "active")
        .order_by(Routine.next_run_at.asc().nulls_last())
    )).scalars().all()

    routines = {
        "active_count": len(active_routines),
        "next_run_at":  active_routines[0].next_run_at.isoformat() if active_routines and active_routines[0].next_run_at else None,
    }

    # ── Current week bounds (half-open: [week_start, next_monday)) ────────────
    week_start_utc, next_monday_utc = _current_week_bounds()
    week_start_ist = week_start_utc.astimezone(IST)
    week_end_ist   = (next_monday_utc - timedelta(microseconds=1)).astimezone(IST)

    # ── Week spend + order count ───────────────────────────────────────────────
    week_agg = (await db.execute(
        select(
            func.coalesce(func.sum(Order.grand_total), 0).label("total_spend"),
            func.count(Order.id).label("order_count"),
        )
        .where(
            Order.household_id == household_id,
            Order.placed_at >= week_start_utc,
            Order.placed_at < next_monday_utc,
        )
    )).one()

    total_spend  = float(week_agg.total_spend)
    order_count  = int(week_agg.order_count)
    budget_max   = float(hh.weekly_budget_max) if hh.weekly_budget_max else None

    # ── Nutrition goals (fall back to ICMR defaults if no row set) ───────────
    goals = (await db.execute(
        select(HouseholdNutritionGoals).where(HouseholdNutritionGoals.household_id == household_id)
    )).scalar_one_or_none()

    members = (await db.execute(
        select(HouseholdMember).where(HouseholdMember.household_id == household_id)
    )).scalars().all()
    icmr = personalised_weekly_targets(members, hh.member_count or 1)

    calorie_target  = (goals.daily_calories  * 7 if goals and goals.daily_calories  else icmr["calories"])
    protein_target  = (goals.daily_protein_g * 7 if goals and goals.daily_protein_g else icmr["protein_g"])
    fiber_target    = (goals.daily_fiber_g   * 7 if goals and goals.daily_fiber_g   else icmr["fiber_g"])
    sodium_target   = (goals.daily_sodium_mg * 7 if goals and goals.daily_sodium_mg else icmr["sodium_mg"])

    # ── Week nutrition totals ─────────────────────────────────────────────────
    nut_agg = (await db.execute(
        select(
            func.sum(OrderNutrition.total_calories).label("calories"),
            func.sum(OrderNutrition.total_protein_g).label("protein_g"),
            func.sum(OrderNutrition.total_fiber_g).label("fiber_g"),
            func.sum(OrderNutrition.total_sodium_mg).label("sodium_mg"),
        )
        .join(Order, Order.id == OrderNutrition.order_id)
        .where(
            OrderNutrition.household_id == household_id,
            Order.placed_at >= week_start_utc,
            Order.placed_at < next_monday_utc,
        )
    )).one()

    # Show nutrition only when this week has resolved data — avoids empty dot bars.
    # Unresolved order_nutrition rows store 0.0, not NULL, so check > 0 not just not None.
    has_any_nutrition = nut_agg.calories is not None and nut_agg.calories > 0

    week = {
        "week_start":       week_start_ist.isoformat(),
        "week_end":         week_end_ist.isoformat(),
        "total_spend":      total_spend,
        "budget_max":       budget_max,
        "order_count":      order_count,
        "total_calories":   float(nut_agg.calories)  if nut_agg.calories  is not None else None,
        "calorie_target":   calorie_target,
        "total_protein_g":  float(nut_agg.protein_g) if nut_agg.protein_g is not None else None,
        "protein_target":   protein_target,
        "total_fiber_g":    float(nut_agg.fiber_g)   if nut_agg.fiber_g   is not None else None,
        "fiber_target":     fiber_target,
        "total_sodium_mg":  float(nut_agg.sodium_mg) if nut_agg.sodium_mg is not None else None,
        "sodium_target":    sodium_target,
        "has_nutrition_data": has_any_nutrition,
    }

    # ── All-time stats ─────────────────────────────────────────────────────────
    all_time = (await db.execute(
        select(
            func.count(Order.id).label("total_orders"),
            func.avg(Order.grand_total).label("avg_order_total"),
        )
        .where(Order.household_id == household_id)
    )).one()

    # Last order's nutrition resolution ratio
    last_on = (await db.execute(
        select(OrderNutrition)
        .join(Order, Order.id == OrderNutrition.order_id)
        .where(OrderNutrition.household_id == household_id)
        .order_by(Order.placed_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    stats = {
        "total_orders":    int(all_time.total_orders),
        "avg_order_total": round(float(all_time.avg_order_total), 2) if all_time.avg_order_total else None,
        "last_nutrition": {
            "resolved_items":   last_on.resolved_items,
            "unresolved_items": last_on.unresolved_items,
            "total_items":      last_on.total_items,
        } if last_on else None,
    }

    # ── Recent orders (last 3) ─────────────────────────────────────────────────
    recent_raw = (await db.execute(
        select(Order)
        .where(Order.household_id == household_id)
        .order_by(Order.placed_at.desc())
        .limit(3)
    )).scalars().all()

    recent_order_ids = [o.id for o in recent_raw]
    items_result = (await db.execute(
        select(OrderItem.order_id, OrderItem.product_name)
        .where(OrderItem.order_id.in_(recent_order_ids))
    )).all()

    # Group items by order_id preserving insertion order
    items_by_order: dict = {}
    for row in items_result:
        items_by_order.setdefault(str(row.order_id), []).append(row.product_name)

    recent_orders = []
    for o in recent_raw:
        all_names     = items_by_order.get(str(o.id), [])
        total_items   = len(all_names)
        preview_names = [n for n in all_names[:2] if n]
        preview       = ", ".join(preview_names)
        extra_count   = max(0, total_items - 2)

        recent_orders.append({
            "placed_at":   o.placed_at.isoformat() if o.placed_at else None,
            "preview":     preview,
            "extra_count": extra_count,
            "total":       float(o.grand_total),
            "order_id":    str(o.id),
        })

    return APIResponse.ok({
        "flow":          flow,
        "routines":      routines,
        "week":          week,
        "stats":         stats,
        "recent_orders": recent_orders,
    })
