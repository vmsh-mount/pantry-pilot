"""
BasketEditingService — all basket mutation logic for the awaiting_confirmation stage.

Used by:
  - app/api/basket.py  (cockpit UI endpoints)
  - app/tasks/whatsapp.py  (WhatsApp intent handlers)
  - app/tasks/planning.py  (add_item_to_basket Celery task)

Methods:
  remove_item(db, loop_run_id, item_id)              → LoopRunItem | None
  remove_items_by_index(db, loop_run_id, indices)    → list[LoopRunItem]
  search_items(db, household_id, query)              → list[MCPProduct]
  add_item(db, loop_run, household_id, product)      → LoopRunItem
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Address, HouseholdPreferences, LoopRun, LoopRunItem
from app.mcp.types import MCPProduct
from app.utils.exceptions import TokenExpiredError, SwiggyMCPError
from app.utils.logging import get_logger

logger = get_logger(__name__)


class BasketEditingService:

    # ── Remove ────────────────────────────────────────────────────────────────

    async def remove_item(
        self,
        db:          AsyncSession,
        loop_run_id: str,
        item_id:     str,
    ) -> LoopRunItem | None:
        """Remove a single basket item by its UUID. Returns the deleted item or None."""
        result = await db.execute(
            select(LoopRunItem).where(
                LoopRunItem.id          == item_id,
                LoopRunItem.loop_run_id == loop_run_id,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            return None
        await db.delete(item)
        await db.commit()
        logger.info("basket_item_removed", loop_run_id=loop_run_id, item_id=item_id, item_name=item.item_name)
        return item

    async def remove_items_by_index(
        self,
        db:          AsyncSession,
        loop_run_id: str,
        indices:     list[int],
    ) -> list[LoopRunItem]:
        """Remove items by 0-based position in the ordered item list. Returns removed items."""
        result = await db.execute(
            select(LoopRunItem)
            .where(LoopRunItem.loop_run_id == loop_run_id)
            .order_by(LoopRunItem.created_at)
        )
        all_items = result.scalars().all()

        removed: list[LoopRunItem] = []
        for idx in sorted(set(indices), reverse=True):
            if 0 <= idx < len(all_items):
                item = all_items[idx]
                removed.append(item)
                await db.delete(item)

        if removed:
            await db.commit()
            logger.info(
                "basket_items_removed_by_index",
                loop_run_id=loop_run_id,
                count=len(removed),
                names=[i.item_name for i in removed],
            )
        return removed

    # ── Search ────────────────────────────────────────────────────────────────

    async def search_items(
        self,
        db:           AsyncSession,
        household_id: str,
        query:        str,
        limit:        int = 10,
    ) -> list[MCPProduct]:
        """
        Live product search via Swiggy MCP.
        Raises TokenExpiredError if the household's Swiggy token is expired or missing.
        Raises SwiggyMCPError on search failure.
        """
        from app.services.auth_service import AuthService
        from app.providers.factory import get_mcp_provider

        auth_svc     = AuthService(db)
        access_token = await auth_svc.get_valid_token(household_id)  # raises TokenExpiredError

        prefs_result = await db.execute(
            select(HouseholdPreferences).where(
                HouseholdPreferences.household_id == household_id
            )
        )
        prefs = prefs_result.scalar_one_or_none()

        # preferred_address_id is our internal UUID FK — must resolve to swiggy_address_id
        swiggy_address_id: str | None = None
        if prefs and prefs.preferred_address_id:
            addr_result = await db.execute(
                select(Address).where(Address.id == prefs.preferred_address_id)
            )
            addr = addr_result.scalar_one_or_none()
            swiggy_address_id = addr.swiggy_address_id if addr else None

        if not swiggy_address_id:
            logger.warning("basket_search_no_address", household_id=household_id)
            raise SwiggyMCPError("No delivery address found — cannot search products.")

        client = get_mcp_provider(access_token)
        result = await client.search_products(query, swiggy_address_id, limit=limit)

        # Normalise: mock returns list, real MCP returns MCPSearchResult
        products = result if isinstance(result, list) else getattr(result, "products", [])
        return [p for p in products if _pattr(p, "in_stock", True)]

    # ── Add ───────────────────────────────────────────────────────────────────

    async def add_item(
        self,
        db:           AsyncSession,
        loop_run:     LoopRun,
        household_id: str,
        product:      MCPProduct | dict,
        item_query:   str = "",
        add_reason:   str = "User added via app",
    ) -> LoopRunItem:
        """
        Append a resolved Swiggy product to a pending basket.
        `product` may be an MCPProduct instance or a raw dict (from WhatsApp flow).
        """
        sku_id      = _pattr(product, "sku_id") or _pattr(product, "id")
        name        = _pattr(product, "name", item_query)
        brand       = _pattr(product, "brand")
        price       = float(_pattr(product, "price", 0) or 0)
        image_url   = _pattr(product, "image_url")
        category    = _pattr(product, "category")

        new_item = LoopRunItem(
            loop_run_id         = loop_run.id,
            household_id        = household_id,
            item_name           = item_query or name,
            swiggy_sku_id       = sku_id,
            swiggy_product_name = name,
            brand               = brand,
            quantity            = 1.0,
            unit                = "unit",
            unit_price          = price,
            total_price         = price,
            added_by            = "user_added",
            add_reason          = add_reason,
        )
        db.add(new_item)
        await db.commit()
        await db.refresh(new_item)

        logger.info(
            "basket_item_added",
            loop_run_id=loop_run.id,
            item_name=new_item.item_name,
            sku_id=sku_id,
        )
        return new_item


def _pattr(obj: object, key: str, default=None):
    """Get attribute from dict or object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
