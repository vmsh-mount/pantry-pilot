"""
Redis-backed basket for Quick Order.

Key:  quick_basket:{household_id}
TTL:  24 hours
Value: JSON list of basket items.

Each item shape:
{
    "id":         str (uuid4, basket-local),
    "item_name":  str,
    "brand":      str | None,
    "sku_id":     str | None,
    "unit":       str,
    "quantity":   int,
    "unit_price": float,
}
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.redis import get_redis

_TTL = 86_400  # 24 hours


def _key(household_id: str) -> str:
    return f"quick_basket:{household_id}"


async def get_basket(household_id: str) -> list[dict]:
    redis = await get_redis()
    raw = await redis.get(_key(household_id))
    return json.loads(raw) if raw else []


async def save_basket(household_id: str, items: list[dict]) -> None:
    redis = await get_redis()
    await redis.set(_key(household_id), json.dumps(items), ex=_TTL)


async def clear_basket(household_id: str) -> None:
    redis = await get_redis()
    await redis.delete(_key(household_id))


async def add_item(household_id: str, item: dict[str, Any]) -> dict:
    """Add an item; returns the basket entry with its generated id."""
    item_name = item.get("item_name") or ""
    if not item_name:
        raise ValueError("item_name is required")

    # NOTE: get→mutate→set is not atomic. Acceptable for a single-user basket;
    # revisit if concurrent edits become an issue.
    items = await get_basket(household_id)
    entry = {
        "id":         str(uuid.uuid4()),
        "item_name":  item_name,
        "brand":      item.get("brand"),
        "sku_id":     item.get("sku_id"),
        "unit":       item.get("unit", "units"),
        "quantity":   int(item.get("quantity", 1)),
        "unit_price": float(item.get("unit_price", 0)),
    }
    items.append(entry)
    await save_basket(household_id, items)
    return entry


async def update_item(
    household_id: str,
    item_id: str,
    quantity: int | None = None,
    brand: str | None = None,
) -> dict | None:
    """Update qty or brand on an existing basket item. Returns updated entry or None if not found."""
    # NOTE: get→mutate→set is not atomic. Acceptable for a single-user basket.
    items = await get_basket(household_id)
    for item in items:
        if item["id"] == item_id:
            if quantity is not None:
                item["quantity"] = max(1, int(quantity))
            if brand is not None:
                item["brand"] = brand
            await save_basket(household_id, items)
            return item
    return None


async def remove_item(household_id: str, item_id: str) -> dict | None:
    """Remove item by id. Returns the removed entry or None if not found."""
    # NOTE: get→mutate→set is not atomic. Acceptable for a single-user basket.
    items = await get_basket(household_id)
    for i, item in enumerate(items):
        if item["id"] == item_id:
            removed = items.pop(i)
            await save_basket(household_id, items)
            return removed
    return None
