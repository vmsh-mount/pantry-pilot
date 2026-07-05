"""
External ID mapper — translates between internal UUIDs and external system IDs.

Keyed by (entity_type, system). Current registrations:
  ("address", "swiggy") — Address.id ↔ Address.swiggy_address_id

To add a new mapping, implement a handler pair and register it in
_GET_HANDLERS / _UPSERT_HANDLERS at the bottom of this file.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging import get_logger

logger = get_logger("id_mapper")


class ExternalIdMapper:
    @staticmethod
    async def get_external_id(
        session: AsyncSession,
        entity_type: str,
        system: str,
        internal_uuid: str,
    ) -> str | None:
        """Given our UUID, return the external system's string ID."""
        handler = _GET_HANDLERS.get((entity_type, system))
        if handler is None:
            raise ValueError(f"No external-ID handler for ({entity_type!r}, {system!r})")
        return await handler(session, internal_uuid)

    @staticmethod
    async def get_or_create_internal_id(
        session: AsyncSession,
        entity_type: str,
        system: str,
        external_id: str,
        **kwargs: Any,
    ) -> str:
        """Given an external string ID, return our UUID — creating a DB record if needed."""
        handler = _UPSERT_HANDLERS.get((entity_type, system))
        if handler is None:
            raise ValueError(f"No upsert handler for ({entity_type!r}, {system!r})")
        return await handler(session, external_id, **kwargs)


# ── Handler: ("address", "swiggy") ────────────────────────────────────────────

async def _get_swiggy_address_id(session: AsyncSession, internal_uuid: str) -> str | None:
    from app.models.db import Address
    result = await session.execute(select(Address).where(Address.id == internal_uuid))
    addr = result.scalar_one_or_none()
    return addr.swiggy_address_id if addr else None


async def _upsert_swiggy_address(
    session: AsyncSession,
    swiggy_address_id: str,
    *,
    household_id: str,
    label: str | None = None,
    is_default: bool = False,
) -> str:
    from app.models.db import Address
    result = await session.execute(
        select(Address).where(
            Address.household_id == household_id,
            Address.swiggy_address_id == swiggy_address_id,
        )
    )
    addr = result.scalar_one_or_none()
    if addr is None:
        addr = Address(
            household_id=household_id,
            swiggy_address_id=swiggy_address_id,
            label=label,
            is_default=is_default,
        )
        session.add(addr)
        await session.flush()
        logger.info(
            "id_mapper_address_created",
            household_id=household_id,
            swiggy_address_id=swiggy_address_id,
            internal_uuid=str(addr.id),
        )
    return str(addr.id)


# ── Registry ───────────────────────────────────────────────────────────────────

_GET_HANDLERS: dict[tuple[str, str], Any] = {
    ("address", "swiggy"): _get_swiggy_address_id,
}

_UPSERT_HANDLERS: dict[tuple[str, str], Any] = {
    ("address", "swiggy"): _upsert_swiggy_address,
}
