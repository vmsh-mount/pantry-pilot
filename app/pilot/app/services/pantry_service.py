"""
PantryService — owns all reads and writes to pantry_items.

Responsibilities:
  1. bootstrap_from_history()  — seed pantry from Swiggy order history at onboarding
  2. apply_decay()             — run consumption decay just before planning loop (SENSE stage)
  3. items_needing_reorder()   — return items below reorder threshold after decay
  4. post_order_update()       — reset stock levels after a confirmed order
  5. adjust_consumption_rate() — learn from user basket edits (keep/remove signals)
  6. upsert_item()             — create or update a single pantry item

Design principles from pantry-state.md:
  - Decay runs at loop trigger time, not continuously (reduce write load)
  - Initial consumption rate = avg_qty_per_order / avg_days_between_orders * 7
  - User removes item → decay rate adjusted down by 15%
  - User keeps item (or 'order now' fires before schedule) → rate unchanged
  - User-triggered "add item" outside schedule → rate adjusted up by 15%
  - After 8+ orders, consumption confidence stabilises

Reorder threshold defaults (% of standard order qty):
  staples        → 30%
  fresh_produce  → 0%  (always reorder — short shelf life)
  dairy          → 20%
  packaged       → 20%

Category normalisation: any string containing the canonical bucket name
maps to that bucket (e.g. "dairy_eggs" → "dairy").
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import PantryItem
from app.utils.logging import get_logger

logger = get_logger(__name__)

# ── Reorder threshold multipliers by category ─────────────────────────────────
_REORDER_THRESHOLDS: dict[str, float] = {
    "staples":       0.30,
    "fresh_produce": 0.0,   # always include in basket
    "dairy":         0.20,
    "packaged":      0.20,
    "grocery":       0.25,  # catch-all
}

# ── Consumption learning step sizes ──────────────────────────────────────────
_RATE_STEP_DOWN = 0.85   # user removed item → multiply rate by this
_RATE_STEP_UP   = 1.15   # user added item early → multiply rate by this
_MIN_CONFIDENCE = 8      # orders before we consider rate stable


class PantryService:
    """All pantry state logic. One instance per request, bound to a DB session."""

    def __init__(self, db: AsyncSession):
        self._db = db

    # ── 1. Bootstrap from Swiggy order history ────────────────────────────────

    async def bootstrap_from_history(
        self,
        household_id: str,
        access_token: str,
        order_limit:  int = 40,
    ) -> int:
        """
        Seed pantry_items from up to `order_limit` recent Swiggy orders.

        Algorithm:
          For each unique item across all fetched orders:
            - Compute avg_qty_per_order and avg_days_between_orders
            - Derive avg_weekly_consumption
            - Estimate current stock: last_ordered_qty − consumed since last order
            - Determine reorder_threshold from category defaults
            - Upsert PantryItem (skip if already exists from a previous bootstrap)

        Returns the number of new items inserted.
        """
        from app.providers.factory import get_mcp_provider
        from app.utils.exceptions import SwiggyMCPError

        client = get_mcp_provider(access_token)
        inserted = 0

        try:
            order_summaries = await client.get_orders(limit=order_limit)
        except SwiggyMCPError as e:
            logger.warning("bootstrap_orders_failed", household_id=household_id, error=str(e))
            return 0

        if not order_summaries:
            logger.info("bootstrap_no_orders", household_id=household_id)
            return 0

        # Collect per-item order records: {item_name → [(qty, category, unit, placed_at)]}
        item_records: dict[str, list[dict]] = {}

        # Only fetch details for the most recent 10 orders to cap latency
        for summary in order_summaries[:10]:
            try:
                order_id = _attr(summary, "order_id")
                detail = await client.get_order_details(order_id)
            except (SwiggyMCPError, Exception):
                continue

            placed_at = _parse_ts(_attr(summary, "placed_at"))

            items = _attr(detail, "items", [])
            for item in items:
                name = _attr(item, "name", "")
                if not name:
                    continue
                if name not in item_records:
                    item_records[name] = []
                item_records[name].append({
                    "qty":       float(_attr(item, "quantity", 1)),
                    "category":  _normalise_category(_attr(item, "category") or "grocery"),
                    "unit":      _attr(item, "unit") or "unit",
                    "placed_at": placed_at,
                    "brand":     _attr(item, "brand"),
                })

        now = datetime.now(timezone.utc)

        for item_name, records in item_records.items():
            if len(records) < 1:
                continue

            # Sort by date ascending
            records.sort(key=lambda r: r["placed_at"] or now)

            category = records[-1]["category"]
            unit     = records[-1]["unit"]

            # Average quantity per order
            avg_qty = sum(r["qty"] for r in records) / len(records)

            # Average days between orders
            if len(records) >= 2:
                dates     = [r["placed_at"] for r in records if r["placed_at"]]
                intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates) - 1)]
                avg_days  = sum(intervals) / len(intervals) if intervals else 14
            else:
                avg_days = 14  # assume 2-week cycle as default

            avg_weekly = (avg_qty / max(avg_days, 1)) * 7

            # Estimate current stock
            last_record   = records[-1]
            last_ordered_at = last_record["placed_at"] or now
            last_qty        = last_record["qty"]
            days_elapsed    = (now - last_ordered_at).days
            consumed        = (days_elapsed / 7) * avg_weekly
            estimated_remaining = max(0.0, last_qty - consumed)

            threshold = _reorder_threshold(category, avg_qty)

            await self.upsert_item(
                household_id            = household_id,
                item_name               = item_name,
                category                = category,
                unit                    = unit,
                last_ordered_qty        = last_qty,
                last_ordered_at         = last_ordered_at,
                avg_weekly_consumption  = round(avg_weekly, 3),
                estimated_qty_remaining = round(estimated_remaining, 3),
                reorder_threshold       = round(threshold, 3),
                times_ordered           = len(records),
                overwrite               = False,   # don't overwrite manual edits
            )
            inserted += 1

        await self._db.commit()
        logger.info("bootstrap_complete", household_id=household_id, items_inserted=inserted)
        return inserted

    # ── 2. Consumption decay ──────────────────────────────────────────────────

    async def apply_decay(self, household_id: str) -> list[PantryItem]:
        """
        Recompute estimated_qty_remaining for all active pantry items.
        Called at SENSE stage — before the planning loop reads pantry state.
        Writes decayed values to DB and returns the updated item list.
        """
        result = await self._db.execute(
            select(PantryItem).where(
                PantryItem.household_id == household_id,
                PantryItem.is_active    == True,
            )
        )
        items = result.scalars().all()
        now   = datetime.now(timezone.utc)

        for item in items:
            item.estimated_qty_remaining = _decay(item, now)

        await self._db.commit()
        logger.info("decay_applied", household_id=household_id, item_count=len(items))
        return items

    # ── 3. Items needing reorder ──────────────────────────────────────────────

    async def items_needing_reorder(
        self,
        household_id: str,
        apply_decay_first: bool = True,
    ) -> list[PantryItem]:
        """
        Return pantry items whose estimated_qty_remaining ≤ reorder_threshold.
        Optionally applies consumption decay before checking (default: True).
        """
        items = (
            await self.apply_decay(household_id)
            if apply_decay_first
            else (await self._db.execute(
                select(PantryItem).where(
                    PantryItem.household_id == household_id,
                    PantryItem.is_active    == True,
                )
            )).scalars().all()
        )

        flagged = [
            i for i in items
            if float(i.estimated_qty_remaining) <= float(i.reorder_threshold)
        ]

        logger.info(
            "reorder_items_computed",
            household_id    = household_id,
            total_items     = len(items),
            flagged_count   = len(flagged),
        )
        return flagged

    # ── 4. Post-order update ──────────────────────────────────────────────────

    async def post_order_update(
        self,
        household_id: str,
        order_items:  list[dict],
    ) -> None:
        """
        Reset pantry stock after a confirmed order.

        order_items: [{"name": str, "quantity": float, "unit": str, "category": str | None}]

        For each ordered item:
          - Set last_ordered_qty = quantity
          - Set last_ordered_at  = now
          - Reset estimated_qty_remaining = quantity   (full stock again)
          - Increment times_ordered
          - Compute new avg_weekly_consumption (rolling update if sufficient history)
        """
        now = datetime.now(timezone.utc)

        for ordered in order_items:
            name = ordered.get("name", "").strip()
            qty  = float(ordered.get("quantity", 1))
            unit = ordered.get("unit", "unit")
            cat  = _normalise_category(ordered.get("category") or "grocery")

            result = await self._db.execute(
                select(PantryItem).where(
                    PantryItem.household_id == household_id,
                    PantryItem.item_name    == name,
                )
            )
            item = result.scalar_one_or_none()

            if item is None:
                # First time we've ordered this item — create a new record
                threshold = _reorder_threshold(cat, qty)
                item = PantryItem(
                    household_id            = household_id,
                    item_name               = name,
                    category                = cat,
                    standard_unit           = unit,
                    last_ordered_qty        = qty,
                    last_ordered_at         = now,
                    estimated_qty_remaining = qty,
                    reorder_threshold       = threshold,
                    avg_weekly_consumption  = qty / 2,   # optimistic default: 2 weeks
                    times_ordered           = 1,
                    consumption_confidence  = 0.0,
                )
                self._db.add(item)
            else:
                # Update existing item
                prev_last_ordered_at = item.last_ordered_at
                item.last_ordered_qty        = qty
                item.last_ordered_at         = now
                item.estimated_qty_remaining = qty
                item.times_ordered           = (item.times_ordered or 0) + 1
                item.standard_unit           = unit

                # Refine avg_weekly_consumption if we have a previous order date
                if prev_last_ordered_at:
                    days_between = (now - prev_last_ordered_at).days
                    if days_between > 0:
                        new_rate = (qty / days_between) * 7
                        old_rate = float(item.avg_weekly_consumption or new_rate)
                        # Exponential moving average: weight recent data more
                        alpha = 0.4 if (item.times_ordered or 1) < _MIN_CONFIDENCE else 0.2
                        item.avg_weekly_consumption = round(
                            alpha * new_rate + (1 - alpha) * old_rate, 3
                        )

                # Update confidence
                item.consumption_confidence = min(
                    1.0, (item.times_ordered or 1) / _MIN_CONFIDENCE
                )

        await self._db.commit()
        logger.info(
            "post_order_update_complete",
            household_id = household_id,
            item_count   = len(order_items),
        )

    # ── 5. Consumption rate adjustment from user signals ──────────────────────

    async def adjust_from_user_signal(
        self,
        household_id: str,
        item_name:    str,
        signal:       str,   # "removed" | "kept" | "added_early"
    ) -> None:
        """
        Adjust avg_weekly_consumption based on basket editing signals.

          removed    → user had more stock than we thought → decay too fast → rate ↓15%
          kept       → no adjustment (confirms current model)
          added_early→ user ran out before schedule → decay too slow → rate ↑15%
        """
        result = await self._db.execute(
            select(PantryItem).where(
                PantryItem.household_id == household_id,
                PantryItem.item_name    == item_name,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            return

        current_rate = float(item.avg_weekly_consumption or 0.5)

        if signal == "removed":
            item.avg_weekly_consumption = round(current_rate * _RATE_STEP_DOWN, 3)
            item.times_removed_by_user  = (item.times_removed_by_user or 0) + 1
            item.last_user_action       = "removed"
            item.last_user_action_at    = datetime.now(timezone.utc)

        elif signal == "kept":
            item.times_kept_by_user = (item.times_kept_by_user or 0) + 1
            item.last_user_action   = "kept"
            item.last_user_action_at = datetime.now(timezone.utc)

        elif signal == "added_early":
            item.avg_weekly_consumption = round(current_rate * _RATE_STEP_UP, 3)
            item.last_user_action       = "added_early"
            item.last_user_action_at    = datetime.now(timezone.utc)

        await self._db.commit()
        logger.info(
            "consumption_rate_adjusted",
            household_id = household_id,
            item_name    = item_name,
            signal       = signal,
            new_rate     = float(item.avg_weekly_consumption or 0),
        )

    # ── 6. Upsert a single item ───────────────────────────────────────────────

    async def upsert_item(
        self,
        household_id:            str,
        item_name:               str,
        category:                str,
        unit:                    str,
        last_ordered_qty:        float,
        last_ordered_at:         Optional[datetime],
        avg_weekly_consumption:  float,
        estimated_qty_remaining: float,
        reorder_threshold:       float,
        times_ordered:           int   = 1,
        overwrite:               bool  = True,
    ) -> PantryItem:
        """
        Create or update a PantryItem.
        If overwrite=False, only creates if the item doesn't exist yet.
        """
        result = await self._db.execute(
            select(PantryItem).where(
                PantryItem.household_id == household_id,
                PantryItem.item_name    == item_name,
            )
        )
        item = result.scalar_one_or_none()

        if item is None:
            item = PantryItem(
                household_id            = household_id,
                item_name               = item_name,
                category                = category,
                standard_unit           = unit,
                last_ordered_qty        = last_ordered_qty,
                last_ordered_at         = last_ordered_at,
                estimated_qty_remaining = estimated_qty_remaining,
                reorder_threshold       = reorder_threshold,
                avg_weekly_consumption  = avg_weekly_consumption,
                times_ordered           = times_ordered,
                consumption_confidence  = min(1.0, times_ordered / _MIN_CONFIDENCE),
            )
            self._db.add(item)
        elif overwrite:
            item.category                = category
            item.standard_unit           = unit
            item.last_ordered_qty        = last_ordered_qty
            item.last_ordered_at         = last_ordered_at
            item.estimated_qty_remaining = estimated_qty_remaining
            item.reorder_threshold       = reorder_threshold
            item.avg_weekly_consumption  = avg_weekly_consumption
            item.times_ordered           = times_ordered
            item.consumption_confidence  = min(1.0, times_ordered / _MIN_CONFIDENCE)

        return item

    # ── Convenience: full pantry snapshot ─────────────────────────────────────

    async def get_all_items(self, household_id: str) -> list[PantryItem]:
        """Return all active pantry items without applying decay."""
        result = await self._db.execute(
            select(PantryItem).where(
                PantryItem.household_id == household_id,
                PantryItem.is_active    == True,
            )
        )
        return result.scalars().all()


# ── Private helpers ───────────────────────────────────────────────────────────

def _attr(obj, name: str, default=None):
    """Access an attribute on an object or key on a dict, with a default."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _decay(item: PantryItem, now: datetime) -> float:
    """
    Return the decayed estimated_qty_remaining.
    Does NOT write to the item — caller is responsible for assignment.
    """
    if not item.last_ordered_at or not item.avg_weekly_consumption:
        return float(item.estimated_qty_remaining or 0)

    last_at = item.last_ordered_at
    # Make timezone-aware if naive
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)

    days_elapsed = (now - last_at).days
    if days_elapsed < 0:
        days_elapsed = 0

    consumed = (days_elapsed / 7) * float(item.avg_weekly_consumption)
    remaining = float(item.last_ordered_qty or 0) - consumed
    return round(max(0.0, remaining), 3)


def _reorder_threshold(category: str, qty: float) -> float:
    """Compute reorder threshold as a fraction of standard order quantity."""
    multiplier = _REORDER_THRESHOLDS.get(category, 0.25)
    return round(qty * multiplier, 3)


def _normalise_category(raw: str) -> str:
    """Map any raw category string to a canonical bucket name."""
    raw = raw.lower().replace(" ", "_")
    if "staple" in raw or raw in ("grains", "pulses", "oils", "flour"):
        return "staples"
    if "fresh" in raw or "produce" in raw or "vegetable" in raw or "fruit" in raw:
        return "fresh_produce"
    if "dairy" in raw or "egg" in raw or "milk" in raw or "cheese" in raw:
        return "dairy"
    if "packaged" in raw or "snack" in raw or "beverage" in raw or "drink" in raw:
        return "packaged"
    return "grocery"


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp string to a timezone-aware datetime."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
