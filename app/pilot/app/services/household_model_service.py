"""
HouseholdModelService — manages the learned HouseholdModel for each household.

Responsibilities:
  1. build_context()          — return model state for planning graph consumption
  2. process_signals()        — persist ItemSignal rows and trigger model update
  3. update_model()           — apply signal-driven mutations to HouseholdModel
  4. handle_lifecycle_event() — handle diet changes, new members, vacations, etc.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.database import AsyncSessionLocal

from app.models.db import HouseholdModel, ItemSignal, LoopRun
from app.utils.logging import get_logger

logger = get_logger(__name__)

UTC = timezone.utc

_VALID_SIGNAL_TYPES = frozenset(
    {"added", "removed", "qty_increased", "qty_decreased", "brand_changed", "accepted"}
)
_EDIT_SIGNAL_TYPES = frozenset(
    {"added", "removed", "qty_increased", "qty_decreased", "brand_changed"}
)


# ── Public API ────────────────────────────────────────────────────────────────


async def build_context(household_id: UUID, db: AsyncSession) -> dict:
    """
    Return the household model as a planning-graph-ready context dict.
    If no HouseholdModel row exists yet, safe defaults are returned.
    """
    result = await db.execute(
        select(HouseholdModel).where(HouseholdModel.household_id == str(household_id))
    )
    model = result.scalars().first()

    if model is None:
        logger.info("household_model_not_found_using_defaults", household_id=str(household_id))
        anchors = []
        exclusions = []
        brand_preferences = {}
        avg_edit_count = 0.0
        reorder_horizon_days = 7
        confirmation_behaviour = {}
    else:
        all_anchors = model.anchors or []
        anchors = [
            a for a in all_anchors
            if a.get("status") in ("confirmed", "provisional")
        ]
        exclusions = [
            a["item_name"] for a in all_anchors
            if a.get("status") == "suppressed"
        ]
        brand_preferences = model.preferences or {}
        avg_edit_count = model.avg_edit_count if model.avg_edit_count is not None else 0.0
        reorder_horizon_days = model.reorder_horizon_days if model.reorder_horizon_days is not None else 7
        confirmation_behaviour = model.confirmation_behaviour or {}

    # Fetch last 10 item signals for this household
    sig_result = await db.execute(
        select(ItemSignal)
        .where(ItemSignal.household_id == str(household_id))
        .order_by(ItemSignal.recorded_at.desc())
        .limit(10)
    )
    recent_signals = sig_result.scalars().all()

    return {
        "anchors": anchors,
        "exclusions": exclusions,
        "brand_preferences": brand_preferences,
        "avg_edit_count": avg_edit_count,
        "reorder_horizon_days": reorder_horizon_days,
        "confirmation_behaviour": confirmation_behaviour,
        "recent_edit_patterns": [
            {
                "item_name": s.item_name,
                "signal_type": s.signal_type,
                "previous_value": s.previous_value,
                "new_value": s.new_value,
                "recorded_at": s.recorded_at.isoformat() if s.recorded_at else None,
            }
            for s in recent_signals
        ],
    }


async def process_signals(
    loop_run_id: UUID,
    signals: list[dict],
) -> None:
    """
    Persist ItemSignal rows for valid signal_types, then update the HouseholdModel.

    Opens its own DB session so it can be fired as a background task after the
    caller's session has already committed and closed.

    signals shape: [{item_name, signal_type, previous_value, new_value}]
    """
    async with AsyncSessionLocal() as db:
        run_result = await db.execute(
            select(LoopRun).where(LoopRun.id == str(loop_run_id))
        )
        loop_run = run_result.scalars().first()
        if loop_run is None:
            logger.warning("process_signals_loop_run_not_found", loop_run_id=str(loop_run_id))
            return

        household_id = loop_run.household_id

        for sig in signals:
            signal_type = sig.get("signal_type")
            if signal_type not in _VALID_SIGNAL_TYPES:
                logger.warning(
                    "process_signals_unknown_signal_type",
                    signal_type=signal_type,
                    loop_run_id=str(loop_run_id),
                )
                continue

            db.add(
                ItemSignal(
                    household_id=str(household_id),
                    loop_run_id=str(loop_run_id),
                    item_name=sig.get("item_name", ""),
                    signal_type=signal_type,
                    previous_value=sig.get("previous_value"),
                    new_value=sig.get("new_value"),
                )
            )

        await db.flush()
        await update_model(household_id, loop_run_id, db)
        await db.commit()

    logger.info(
        "process_signals_complete",
        loop_run_id=str(loop_run_id),
        household_id=str(household_id),
        signal_count=len(signals),
    )


async def update_model(
    household_id: UUID,
    loop_run_id: UUID | None,
    db: AsyncSession,
) -> None:
    """
    Apply signal-driven mutations to the HouseholdModel for household_id.
    Fetch-or-create the model, then apply anchor promotions, demotion reviews,
    and rolling edit-count average.
    """
    # Fetch or create HouseholdModel
    result = await db.execute(
        select(HouseholdModel).where(HouseholdModel.household_id == str(household_id))
    )
    model = result.scalars().first()

    if model is None:
        model = HouseholdModel(
            household_id=str(household_id),
            anchors=[],
            preferences={},
            confirmation_behaviour={},
            avg_edit_count=0.0,
            reorder_horizon_days=7,
        )
        db.add(model)
        await db.flush()

    # Fetch signals: for Flow runs filter by loop_run_id; for Quick Order (loop_run_id=None)
    # filter by household and NULL loop_run_id to avoid str(None) = "None" mismatch.
    from sqlalchemy import null as sql_null
    loop_run_filter = (
        ItemSignal.loop_run_id == str(loop_run_id)
        if loop_run_id is not None
        else ItemSignal.loop_run_id.is_(None)
    )
    sig_result = await db.execute(
        select(ItemSignal).where(
            ItemSignal.household_id == str(household_id),
            loop_run_filter,
        )
    )
    current_signals = sig_result.scalars().all()

    current_anchors: list[dict] = list(model.anchors or [])
    anchor_names = {a["item_name"] for a in current_anchors}
    now_iso = datetime.now(UTC).isoformat()

    # ── Anchor promotion: items added in this run ─────────────────────────────
    added_items = [s.item_name for s in current_signals if s.signal_type == "added"]
    new_anchors = list(current_anchors)
    for item_name in added_items:
        if item_name not in anchor_names:
            new_anchors = [
                *new_anchors,
                {
                    "item_name": item_name,
                    "status": "provisional",
                    "promoted_by": "add_back",
                    "promoted_at": now_iso,
                },
            ]
            anchor_names.add(item_name)

    model.anchors = new_anchors
    flag_modified(model, "anchors")

    # ── Demotion review: items removed in 2 consecutive runs ─────────────────
    removed_items = {s.item_name for s in current_signals if s.signal_type == "removed"}

    if removed_items:
        # Find the immediately prior LoopRun for this household
        prior_run_result = await db.execute(
            select(LoopRun)
            .where(
                LoopRun.household_id == str(household_id),
                LoopRun.id != str(loop_run_id),
            )
            .order_by(LoopRun.created_at.desc())
            .limit(1)
        )
        prior_run = prior_run_result.scalars().first()

        if prior_run is not None:
            prior_removed_result = await db.execute(
                select(ItemSignal).where(
                    ItemSignal.household_id == str(household_id),
                    ItemSignal.loop_run_id == str(prior_run.id),
                    ItemSignal.signal_type == "removed",
                )
            )
            prior_removed_items = {s.item_name for s in prior_removed_result.scalars().all()}

            # Items removed in both the prior run and this run
            consecutive_removed = removed_items & prior_removed_items

            if consecutive_removed:
                updated_anchors = []
                changed = False
                for anchor in model.anchors:
                    if (
                        anchor["item_name"] in consecutive_removed
                        and anchor.get("status") == "confirmed"
                    ):
                        updated_anchors.append({**anchor, "status": "demotion_review"})
                        changed = True
                    else:
                        updated_anchors.append(anchor)

                if changed:
                    model.anchors = updated_anchors
                    flag_modified(model, "anchors")

    # ── Rolling avg_edit_count ────────────────────────────────────────────────
    edit_count = sum(
        1 for s in current_signals if s.signal_type in _EDIT_SIGNAL_TYPES
    )
    old = model.avg_edit_count or 0.0
    model.avg_edit_count = (old * 9 + edit_count) / 10

    model.last_updated = datetime.now(UTC)

    logger.info(
        "household_model_updated",
        household_id=str(household_id),
        loop_run_id=str(loop_run_id),
        edit_count=edit_count,
        avg_edit_count=model.avg_edit_count,
    )


async def handle_lifecycle_event(
    household_id: UUID,
    event_type: str,
    db: AsyncSession,
) -> None:
    """
    Apply a lifecycle event mutation to the HouseholdModel.

    Supported events:
      diet_change      — flip confirmed anchors to "under_review", clear last_evaluated_at
      new_member       — reset reorder_horizon_days and avg_edit_count
      vacation_return  — touch last_updated
      major_restock    — touch last_updated
    """
    result = await db.execute(
        select(HouseholdModel).where(HouseholdModel.household_id == str(household_id))
    )
    model = result.scalars().first()

    if model is None:
        model = HouseholdModel(
            household_id=str(household_id),
            anchors=[],
            preferences={},
            confirmation_behaviour={},
            avg_edit_count=0.0,
            reorder_horizon_days=7,
        )
        db.add(model)
        await db.flush()

    if event_type == "diet_change":
        updated_anchors = [
            {**a, "status": "under_review"} if a.get("status") == "confirmed" else a
            for a in (model.anchors or [])
        ]
        model.anchors = updated_anchors
        flag_modified(model, "anchors")
        model.last_evaluated_at = None

    elif event_type == "new_member":
        model.reorder_horizon_days = None
        model.avg_edit_count = None

    elif event_type in ("vacation_return", "major_restock"):
        pass  # just touch last_updated below

    else:
        raise ValueError(f"Unknown lifecycle event: {event_type}")

    model.last_updated = datetime.now(UTC)

    await db.commit()
    logger.info(
        "lifecycle_event_handled",
        household_id=str(household_id),
        event_type=event_type,
    )
