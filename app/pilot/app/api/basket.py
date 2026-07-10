"""
Basket API — dashboard endpoints for viewing and acting on pending baskets.
"""

from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.database import get_db
from app.schemas.common import APIResponse, BasketItemAdd, BasketSearchResult
from app.models.db import LoopRun, LoopRunItem, HouseholdPreferences, Household, FlowBasket, ItemSignal
from app.services.basket_editing_service import BasketEditingService
from app.utils.exceptions import TokenExpiredError, SwiggyMCPError
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/basket", tags=["basket"])


def _build_confirm_signals(loop_run_id: str, final_items: list, generated_items: list) -> list[dict]:
    """Compare final confirmed basket to AI-generated items and return signal dicts."""
    generated_names = {i.get("item_name", "").lower(): i for i in generated_items}
    final_names = {i.item_name.lower(): i for i in final_items}

    signals = []
    for name_lower, item in final_names.items():
        if name_lower in generated_names:
            signals.append({
                "item_name": item.item_name,
                "signal_type": "accepted",
                "previous_value": None,
                "new_value": {"quantity": float(item.quantity or 0), "brand": item.brand},
            })
        else:
            signals.append({
                "item_name": item.item_name,
                "signal_type": "added",
                "previous_value": None,
                "new_value": {"quantity": float(item.quantity or 0), "brand": item.brand},
            })

    for name_lower, gen_item in generated_names.items():
        if name_lower not in final_names:
            signals.append({
                "item_name": gen_item.get("item_name", name_lower),
                "signal_type": "removed",
                "previous_value": {"quantity": gen_item.get("quantity"), "brand": gen_item.get("brand")},
                "new_value": None,
            })

    return signals


def _household_id(request: Request) -> str | None:
    """Return the household_id from session, or None if unauthenticated."""
    return request.session.get("household_id")


IN_PROGRESS_STATES = {"pending", "sensing", "planning", "optimizing", "confirmed", "placing"}

async def _assert_onboarding_complete(household_id: str, db: AsyncSession):
    """Returns 'ok', 'not_found', or 'incomplete'."""
    result = await db.execute(
        select(Household).where(Household.id == household_id)
    )
    household = result.scalar_one_or_none()
    if not household:
        return "not_found"
    if not household.onboarding_complete:
        return "incomplete"
    return "ok"

@router.get("/pending", response_model=APIResponse)
async def get_pending_basket(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the basket currently awaiting user confirmation, if any."""
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    status = await _assert_onboarding_complete(household_id, db)
    if status == "not_found":
        return APIResponse.fail("NOT_AUTHENTICATED", "Session expired. Please log in again.")
    if status == "incomplete":
        return APIResponse.fail("ONBOARDING_INCOMPLETE", "Onboarding not complete.")

    # Check for a basket ready to confirm
    result = await db.execute(
        select(LoopRun)
        .where(
            LoopRun.household_id == household_id,
            LoopRun.state == "awaiting_confirmation",
        )
        .order_by(desc(LoopRun.triggered_at))
        .limit(1)
    )
    loop_run = result.scalar_one_or_none()

    if not loop_run:
        # Check if a run is currently in progress
        in_progress_result = await db.execute(
            select(LoopRun)
            .where(
                LoopRun.household_id == household_id,
                LoopRun.state.in_(IN_PROGRESS_STATES),
            )
            .order_by(desc(LoopRun.triggered_at))
            .limit(1)
        )
        in_progress = in_progress_result.scalar_one_or_none()
        if in_progress:
            return APIResponse.ok({
                "pending":     False,
                "in_progress": True,
                "run_state":   in_progress.state,
                "triggered_at": in_progress.triggered_at.isoformat(),
            })

        # Check for a recently failed run
        failed_result = await db.execute(
            select(LoopRun)
            .where(
                LoopRun.household_id == household_id,
                LoopRun.state == "failed",
            )
            .order_by(desc(LoopRun.triggered_at))
            .limit(1)
        )
        failed = failed_result.scalar_one_or_none()

        prefs_result = await db.execute(
            select(HouseholdPreferences).where(
                HouseholdPreferences.household_id == household_id
            )
        )
        prefs = prefs_result.scalar_one_or_none()
        return APIResponse.ok({
            "pending":      False,
            "in_progress":  False,
            "last_failed":  failed is not None,
            "next_run_at":  prefs.next_run_at.isoformat() if prefs and prefs.next_run_at else None,
        })

    # Load items
    items_result = await db.execute(
        select(LoopRunItem).where(LoopRunItem.loop_run_id == loop_run.id)
    )
    items = items_result.scalars().all()

    return APIResponse.ok({
        "pending":      True,
        "loop_run_id":  loop_run.id,
        "triggered_at": loop_run.triggered_at.isoformat(),
        "confirm_sent_at": loop_run.confirm_sent_at.isoformat() if loop_run.confirm_sent_at else None,
        "items": [
            {
                "id":           i.id,
                "item_name":    i.item_name,
                "brand":        i.brand,
                "product_name": i.swiggy_product_name,
                "sku_id":       i.swiggy_sku_id,
                "quantity":     float(i.quantity),
                "unit":         i.unit,
                "unit_price":   float(i.unit_price) if i.unit_price else 0.0,
                "total_price":  float(i.total_price) if i.total_price else 0.0,
                "is_substitution": i.is_substitution,
                "original_item_name": i.original_item_name,
                "category":     None,
                "added_by":     i.added_by,
                "add_reason":   i.add_reason,
            }
            for i in items
        ],
        "estimated_total": sum(float(i.total_price) for i in items),
        "item_count":      len(items),
    })


@router.post("/confirm", response_model=APIResponse)
async def confirm_basket(request: Request, db: AsyncSession = Depends(get_db)):
    """User approves the pending basket — triggers order placement."""
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    result = await db.execute(
        select(LoopRun).where(
            LoopRun.household_id == household_id,
            LoopRun.state == "awaiting_confirmation",
        ).limit(1)
    )
    loop_run = result.scalar_one_or_none()
    if not loop_run:
        return APIResponse.fail("NO_PENDING_BASKET", "No basket is waiting for confirmation.")

    # Guard: don't confirm an empty basket
    items_check = await db.execute(
        select(LoopRunItem).where(LoopRunItem.loop_run_id == loop_run.id).limit(1)
    )
    if not items_check.scalar_one_or_none():
        return APIResponse.fail("EMPTY_BASKET", "Cannot confirm an empty basket — no items were resolved.")

    # Load final basket items
    final_items_result = await db.execute(
        select(LoopRunItem).where(LoopRunItem.loop_run_id == loop_run.id)
    )
    final_items = final_items_result.scalars().all()

    # Look up FlowBasket for generated_items reference
    flow_basket_result = await db.execute(
        select(FlowBasket).where(FlowBasket.loop_run_id == loop_run.id).limit(1)
    )
    flow_basket = flow_basket_result.scalar_one_or_none()
    generated_items = flow_basket.generated_items if flow_basket else []

    # Transition state and commit FIRST — placement task must see "confirmed"
    loop_run.state       = "confirmed"
    loop_run.user_action = "confirmed"
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    loop_run.confirm_responded_at = now

    if flow_basket:
        flow_basket.status = "confirmed"
        flow_basket.delivered_at = now

    await db.commit()

    # Enqueue placement task after commit so worker sees confirmed state
    from app.tasks.planning import place_confirmed_order
    place_confirmed_order.delay(household_id, loop_run.id)

    # Emit ItemSignals after successful commit — decoupled from main transaction
    # so a signal-processing failure cannot roll back the confirmation.
    import asyncio
    from app.services.household_model_service import process_signals
    signals = _build_confirm_signals(loop_run.id, final_items, generated_items)
    asyncio.create_task(process_signals(loop_run.id, signals))

    logger.info("basket_confirmed_via_ui", household_id=household_id, loop_run_id=loop_run.id)
    return APIResponse.ok({"confirmed": True, "loop_run_id": loop_run.id})


@router.post("/skip", response_model=APIResponse)
async def skip_basket(request: Request, db: AsyncSession = Depends(get_db)):
    """User skips the pending basket this week."""
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    result = await db.execute(
        select(LoopRun).where(
            LoopRun.household_id == household_id,
            LoopRun.state == "awaiting_confirmation",
        ).limit(1)
    )
    loop_run = result.scalar_one_or_none()
    if not loop_run:
        # Graceful no-op — nothing to skip
        return APIResponse.ok({"skipped": False, "message": "No basket is pending."})

    loop_run.state       = "skipped"
    loop_run.user_action = "skipped"
    loop_run.skip_reason = "user_skipped"
    from datetime import datetime, timezone
    loop_run.confirm_responded_at = datetime.now(timezone.utc)
    await db.commit()

    # Reschedule next run
    from app.services.planning_service import PlanningService
    await PlanningService(db).reschedule_next_run(household_id)

    logger.info("basket_skipped_via_ui", household_id=household_id, loop_run_id=loop_run.id)
    return APIResponse.ok({"skipped": True})


@router.post("/trigger", response_model=APIResponse)
async def trigger_basket(request: Request, db: AsyncSession = Depends(get_db)):
    """Manually trigger a new basket run right now."""
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    _onboard_status = await _assert_onboarding_complete(household_id, db)
    if _onboard_status == "not_found":
        return APIResponse.fail("NOT_AUTHENTICATED", "Session expired. Please log in again.")
    if _onboard_status == "incomplete":
        return APIResponse.fail("ONBOARDING_INCOMPLETE", "Onboarding not complete.")

    # Reject paused households
    hh_result = await db.execute(select(Household).where(Household.id == household_id))
    hh = hh_result.scalar_one_or_none()
    if hh and hh.is_paused:
        return APIResponse.fail("HOUSEHOLD_PAUSED", "Your account is paused.")

    # Don't start a new run if one is already pending
    result = await db.execute(
        select(LoopRun).where(
            LoopRun.household_id == household_id,
            LoopRun.state.in_(["pending", "sensing", "planning", "optimizing", "awaiting_confirmation"]),
        ).limit(1)
    )
    if result.scalar_one_or_none():
        return APIResponse.fail("RUN_IN_PROGRESS", "A basket run is already in progress.")

    from app.tasks.planning import trigger_planning_loop
    trigger_planning_loop.delay(household_id, "user_triggered")

    logger.info("basket_triggered_via_ui", household_id=household_id)
    return APIResponse.ok({"triggered": True})


# ── Basket editing endpoints ───────────────────────────────────────────────────

async def _get_pending_run(household_id: str, db: AsyncSession) -> LoopRun | None:
    result = await db.execute(
        select(LoopRun).where(
            LoopRun.household_id == household_id,
            LoopRun.state        == "awaiting_confirmation",
        ).limit(1)
    )
    return result.scalar_one_or_none()


@router.delete("/item/{item_id}", response_model=APIResponse)
async def remove_basket_item(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Remove a single item from the pending basket by its UUID."""
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    loop_run = await _get_pending_run(household_id, db)
    if not loop_run:
        return APIResponse.fail("NO_PENDING_BASKET", "No basket is waiting for confirmation.")

    # Fetch item before removal to capture previous_value
    item_before_result = await db.execute(
        select(LoopRunItem).where(LoopRunItem.id == item_id)
    )
    item_before = item_before_result.scalar_one_or_none()

    # Write signal before applying the edit
    if item_before:
        signal = ItemSignal(
            household_id=household_id,
            loop_run_id=loop_run.id,
            item_name=item_before.item_name,
            signal_type="removed",
            previous_value={"quantity": float(item_before.quantity or 0), "brand": item_before.brand},
            new_value=None,
        )
        db.add(signal)
        await db.flush()

    svc     = BasketEditingService()
    removed = await svc.remove_item(db, loop_run.id, item_id)
    if removed is None:
        return APIResponse.fail("ITEM_NOT_FOUND", "Item not found in basket.")

    # Recompute estimated total after removal
    items_result = await db.execute(
        select(LoopRunItem).where(LoopRunItem.loop_run_id == loop_run.id)
    )
    remaining = items_result.scalars().all()

    logger.info("basket_item_removed_via_ui", household_id=household_id, item_id=item_id)
    return APIResponse.ok({
        "removed":         True,
        "item_name":       removed.item_name,
        "estimated_total": sum(float(i.total_price or 0) for i in remaining),
        "item_count":      len(remaining),
    })


@router.get("/search", response_model=APIResponse)
async def search_basket_items(q: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Live Swiggy product search — proxied through the household's MCP token."""
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    if not q or len(q.strip()) < 2:
        return APIResponse.fail("QUERY_TOO_SHORT", "Search query must be at least 2 characters.")

    svc = BasketEditingService()
    try:
        products = await svc.search_items(db, household_id, q.strip())
    except TokenExpiredError:
        return APIResponse.fail("TOKEN_EXPIRED", "Your Swiggy session has expired. Please re-authenticate.", retryable=False)
    except SwiggyMCPError as e:
        return APIResponse.fail("SEARCH_FAILED", str(e), retryable=True)

    results = [
        BasketSearchResult(
            swiggy_product_id = getattr(p, "sku_id", None) or (p.get("sku_id") if isinstance(p, dict) else ""),
            name              = getattr(p, "name", None) or (p.get("name", "") if isinstance(p, dict) else ""),
            price             = float(getattr(p, "price", 0) or (p.get("price", 0) if isinstance(p, dict) else 0)),
            image_url         = getattr(p, "image_url", None) or (p.get("image_url") if isinstance(p, dict) else None),
            brand             = getattr(p, "brand", None) or (p.get("brand") if isinstance(p, dict) else None),
        )
        for p in products
    ]

    return APIResponse.ok({"results": [r.model_dump() for r in results]})


@router.post("/item", response_model=APIResponse)
async def add_basket_item(body: BasketItemAdd, request: Request, db: AsyncSession = Depends(get_db)):
    """Add a Swiggy product (selected from search results) to the pending basket."""
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    loop_run = await _get_pending_run(household_id, db)
    if not loop_run:
        return APIResponse.fail("NO_PENDING_BASKET", "No basket is waiting for confirmation.")

    svc = BasketEditingService()

    # Build a product-like dict from the request body
    product = {
        "sku_id":    body.swiggy_product_id,
        "name":      body.name,
        "price":     body.price,
        "image_url": body.image_url,
        "category":  body.category,
        "brand":     body.brand,
        "in_stock":  True,
    }

    # Write signal before adding (previous_value = None for new adds)
    signal = ItemSignal(
        household_id=household_id,
        loop_run_id=loop_run.id,
        item_name=body.name,
        signal_type="added",
        previous_value=None,
        new_value={"quantity": 1, "brand": body.brand, "sku_id": body.swiggy_product_id},
    )
    db.add(signal)
    await db.flush()

    new_item = await svc.add_item(
        db, loop_run, household_id, product,
        item_query=body.name,
        add_reason="User added via app",
    )

    items_result = await db.execute(
        select(LoopRunItem).where(LoopRunItem.loop_run_id == loop_run.id)
    )
    all_items = items_result.scalars().all()

    logger.info("basket_item_added_via_ui", household_id=household_id, item_name=body.name)
    return APIResponse.ok({
        "added": True,
        "item": {
            "id":           new_item.id,
            "item_name":    new_item.item_name,
            "brand":        new_item.brand,
            "product_name": new_item.swiggy_product_name,
            "sku_id":       new_item.swiggy_sku_id,
            "quantity":     float(new_item.quantity),
            "unit":         new_item.unit,
            "unit_price":   float(new_item.unit_price or 0),
            "total_price":  float(new_item.total_price or 0),
            "is_substitution": False,
            "added_by":     new_item.added_by,
            "category":     body.category,
        },
        "estimated_total": sum(float(i.total_price or 0) for i in all_items),
        "item_count":      len(all_items),
    })
