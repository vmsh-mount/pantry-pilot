"""
Runs API — visibility into LoopRun history, stats, and per-run item detail.
"""

from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.database import get_db
from app.schemas.common import APIResponse
from app.models.db import LoopRun, LoopRunItem, HouseholdPreferences, Order
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/runs", tags=["runs"])

# Maps UI badge labels → real DB states.
# Never query WHERE state = 'in_progress' — that literal value doesn't exist.
STATUS_MAP: dict[str, list[str]] = {
    "in_progress":           ["pending", "sensing", "planning", "optimizing", "confirmed", "placing"],
    "awaiting_confirmation": ["awaiting_confirmation"],
    "completed":             ["completed"],
    "failed":                ["failed"],
    "skipped":               ["skipped"],
}


def _household_id(request: Request) -> str | None:
    return request.session.get("household_id")


@router.get("", response_model=APIResponse)
async def list_runs(
    request:  Request,
    status:   str | None = None,
    limit:    int        = 20,
    offset:   int        = 0,
    db:       AsyncSession = Depends(get_db),
):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    if status and status not in STATUS_MAP:
        # Unknown badge label — return empty rather than ignoring the filter
        return APIResponse.ok({
            "runs": [], "filtered_count": 0, "next_run_at": None,
            "stats": {"total_runs": 0, "last_order_total": None, "avg_order_total": None},
        })
    states = STATUS_MAP.get(status) if status else None

    # ── Item count + estimated total per run (subquery) ───────────────────────
    item_sq = (
        select(
            LoopRunItem.loop_run_id,
            func.count(LoopRunItem.id).label("item_count"),
            func.sum(LoopRunItem.total_price).label("items_total"),
        )
        .group_by(LoopRunItem.loop_run_id)
        .subquery()
    )

    # ── Runs list ─────────────────────────────────────────────────────────────
    runs_q = (
        select(LoopRun, item_sq.c.item_count, item_sq.c.items_total, Order.grand_total)
        .outerjoin(item_sq, LoopRun.id == item_sq.c.loop_run_id)
        .outerjoin(Order, LoopRun.order_id == Order.id)
        .where(LoopRun.household_id == household_id)
    )
    if states:
        runs_q = runs_q.where(LoopRun.state.in_(states))
    runs_q = runs_q.order_by(desc(LoopRun.triggered_at)).limit(limit).offset(offset)

    runs_result = await db.execute(runs_q)
    rows = runs_result.all()

    # ── filtered_count ────────────────────────────────────────────────────────
    count_q = select(func.count(LoopRun.id)).where(LoopRun.household_id == household_id)
    if states:
        count_q = count_q.where(LoopRun.state.in_(states))
    filtered_count = (await db.execute(count_q)).scalar_one()

    # When no filter, filtered_count == total_runs — reuse, no extra query.
    if not states:
        total_runs = filtered_count
    else:
        total_runs_result = await db.execute(
            select(func.count(LoopRun.id)).where(LoopRun.household_id == household_id)
        )
        total_runs = total_runs_result.scalar_one()

    # ── Stats ─────────────────────────────────────────────────────────────────
    # last_order_total: grand_total of the most recent completed run
    last_order_result = await db.execute(
        select(Order.grand_total)
        .join(LoopRun, LoopRun.order_id == Order.id)
        .where(LoopRun.household_id == household_id, LoopRun.state == "completed")
        .order_by(desc(Order.placed_at))
        .limit(1)
    )
    last_order_total = last_order_result.scalar_one_or_none()

    # avg_order_total: SQL AVG over completed orders
    avg_result = await db.execute(
        select(func.avg(Order.grand_total))
        .join(LoopRun, LoopRun.order_id == Order.id)
        .where(LoopRun.household_id == household_id, LoopRun.state == "completed")
    )
    avg_order_total = avg_result.scalar_one_or_none()

    # ── next_run_at ───────────────────────────────────────────────────────────
    prefs_result = await db.execute(
        select(HouseholdPreferences).where(HouseholdPreferences.household_id == household_id)
    )
    prefs = prefs_result.scalar_one_or_none()
    next_run_at = prefs.next_run_at.isoformat() if prefs and prefs.next_run_at else None

    # ── Serialise runs ────────────────────────────────────────────────────────
    serialised = []
    for run, item_count, items_total, grand_total in rows:
        # Use Order.grand_total for completed runs; fall back to LoopRunItem sum
        total_price = float(grand_total) if grand_total is not None else (
            float(items_total) if items_total is not None else None
        )
        serialised.append({
            "id":             run.id,
            "state":          run.state,
            "triggered_at":   run.triggered_at.isoformat(),
            "completed_at":   run.place_completed_at.isoformat() if run.place_completed_at else None,
            "item_count":     int(item_count) if item_count else 0,
            "total_price":    total_price,
            "failure_reason": run.failure_reason,
            "failure_stage":  run.failure_stage,
            "skip_reason":    run.skip_reason,
            "order_id":       str(run.order_id) if run.order_id else None,
        })

    return APIResponse.ok({
        "runs":           serialised,
        "filtered_count": filtered_count,
        "next_run_at":    next_run_at,
        "stats": {
            "total_runs":       total_runs,
            "last_order_total": float(last_order_total) if last_order_total is not None else None,
            "avg_order_total":  round(float(avg_order_total), 2) if avg_order_total is not None else None,
        },
    })


@router.get("/{run_id}/items", response_model=APIResponse)
async def get_run_items(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    # Ownership check — return 404 on mismatch to avoid leaking run existence
    run_result = await db.execute(
        select(LoopRun).where(LoopRun.id == run_id)
    )
    run = run_result.scalar_one_or_none()
    if not run or run.household_id != household_id:
        return APIResponse.fail("NOT_FOUND", "Run not found.")

    items_result = await db.execute(
        select(LoopRunItem)
        .where(LoopRunItem.loop_run_id == run_id)
        .order_by(LoopRunItem.created_at)
    )
    items = items_result.scalars().all()

    return APIResponse.ok({
        "items": [
            {
                "item_name":          i.item_name,
                "swiggy_product_name": i.swiggy_product_name,
                "brand":              i.brand,
                "quantity":           float(i.quantity),
                "unit":               i.unit,
                "total_price":        float(i.total_price) if i.total_price else None,
                "added_by":           i.added_by,
                "is_substitution":    i.is_substitution,
                "original_item_name": i.original_item_name,
            }
            for i in items
        ]
    })
