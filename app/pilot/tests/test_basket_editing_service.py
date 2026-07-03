"""
Unit tests — BasketEditingService

Covers:
  remove_item
    1.  Removes item by UUID, returns deleted item
    2.  Returns None for unknown item_id
    3.  Guards against cross-run deletion (item belongs to different run)

  remove_items_by_index
    4.  Removes single item by 0-based index
    5.  Removes multiple items, indices sorted correctly
    6.  Out-of-range index silently ignored
    7.  Duplicate indices deduplicated
    8.  Empty indices list → no items removed, no commit

  add_item
    9.  Creates LoopRunItem with correct fields, added_by="user_added"
    10. MCPProduct and dict product both work (via _pattr)
    11. item_query takes priority over product.name as item_name

  search_items
    12. TokenExpiredError raised when token is expired
    13. No delivery address → SwiggyMCPError raised
    14. Out-of-stock products filtered out
    15. Returns MCPProduct list on success

  _pattr helper
    16. Reads from dict by key
    17. Reads from object by attribute
    18. Returns default when missing from both
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from app.services.basket_editing_service import BasketEditingService, _pattr
from app.utils.exceptions import TokenExpiredError, SwiggyMCPError


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def svc():
    return BasketEditingService()


@pytest_asyncio.fixture
async def db(tmp_path):
    """In-process SQLite database for unit-test isolation — no Docker required."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.models.db import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()


async def _seed_household(db):
    """Minimal household + token + preferences + address."""
    from app.models.db import Household, SwiggyToken, HouseholdPreferences, Address
    from app.utils.crypto import encrypt_token

    hh = Household(
        swiggy_user_id      = "swiggy_test_001",
        household_type      = "couple",
        member_count        = 2,
        diet_type           = "vegetarian",
        allergies           = [],
        weekly_budget_min   = 1500,
        weekly_budget_max   = 2500,
        onboarding_complete = True,
        is_active           = True,
        is_paused           = False,
    )
    db.add(hh)
    await db.flush()

    token = SwiggyToken(
        household_id     = hh.id,
        access_token_enc = encrypt_token("fake_access_token"),
        token_expiry     = datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(token)

    addr = Address(
        household_id      = hh.id,
        swiggy_address_id = "addr_home_001",
        label             = "Home",
        is_default        = True,
    )
    db.add(addr)
    await db.flush()

    prefs = HouseholdPreferences(
        household_id            = hh.id,
        preferred_order_day     = "sunday",
        preferred_delivery_slot = "evening",
        preferred_address_id    = addr.id,
    )
    db.add(prefs)
    await db.commit()
    return hh, addr


async def _seed_loop_run(db, household_id, state="awaiting_confirmation"):
    from app.models.db import LoopRun
    run = LoopRun(
        household_id = household_id,
        trigger_type = "scheduled",
        state        = state,
        triggered_at = datetime.now(timezone.utc),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _seed_item(db, loop_run_id, household_id, name="Tata Salt", price=28.0):
    from app.models.db import LoopRunItem
    item = LoopRunItem(
        loop_run_id         = loop_run_id,
        household_id        = household_id,
        item_name           = name,
        swiggy_sku_id       = f"sku_{name.lower().replace(' ', '_')}",
        swiggy_product_name = f"{name} 1kg",
        brand               = "Test Brand",
        quantity            = 1.0,
        unit                = "kg",
        unit_price          = price,
        total_price         = price,
        added_by            = "rules_engine",
        is_substitution     = False,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


# ══════════════════════════════════════════════════════════════════════════════
# remove_item
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_remove_item_returns_deleted_item(svc, db):
    """Removing an existing item returns the item and deletes it from DB."""
    hh, _ = await _seed_household(db)
    run    = await _seed_loop_run(db, hh.id)
    item   = await _seed_item(db, run.id, hh.id)

    result = await svc.remove_item(db, run.id, item.id)

    assert result is not None
    assert result.item_name == "Tata Salt"

    # Confirm gone from DB
    from sqlalchemy import select
    from app.models.db import LoopRunItem
    check = await db.execute(select(LoopRunItem).where(LoopRunItem.id == item.id))
    assert check.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_remove_item_unknown_id_returns_none(svc, db):
    """Non-existent item_id → returns None, no crash."""
    hh, _ = await _seed_household(db)
    run    = await _seed_loop_run(db, hh.id)

    result = await svc.remove_item(db, run.id, "00000000-0000-0000-0000-000000000000")
    assert result is None


@pytest.mark.asyncio
async def test_remove_item_cross_run_blocked(svc, db):
    """item_id from a different run → not found (loop_run_id guard)."""
    hh, _  = await _seed_household(db)
    run1   = await _seed_loop_run(db, hh.id)
    run2   = await _seed_loop_run(db, hh.id)
    item   = await _seed_item(db, run1.id, hh.id)

    # Try to remove item belonging to run1 while specifying run2
    result = await svc.remove_item(db, run2.id, item.id)
    assert result is None

    # Item still exists
    from sqlalchemy import select
    from app.models.db import LoopRunItem
    check = await db.execute(select(LoopRunItem).where(LoopRunItem.id == item.id))
    assert check.scalar_one_or_none() is not None


# ══════════════════════════════════════════════════════════════════════════════
# remove_items_by_index
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_remove_by_index_single(svc, db):
    """Remove item at index 0 from a 2-item basket."""
    hh, _ = await _seed_household(db)
    run   = await _seed_loop_run(db, hh.id)
    item1 = await _seed_item(db, run.id, hh.id, name="Salt",   price=28.0)
    item2 = await _seed_item(db, run.id, hh.id, name="Milk",   price=64.0)

    removed = await svc.remove_items_by_index(db, run.id, [0])

    assert len(removed) == 1
    # Oldest item (item1) should be at index 0 (ordered by created_at)

    from sqlalchemy import select
    from app.models.db import LoopRunItem
    remaining = (await db.execute(select(LoopRunItem).where(LoopRunItem.loop_run_id == run.id))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].item_name == "Milk"


@pytest.mark.asyncio
async def test_remove_by_index_multiple(svc, db):
    """Remove items at indices 0 and 2 from a 3-item basket."""
    hh, _ = await _seed_household(db)
    run   = await _seed_loop_run(db, hh.id)
    await _seed_item(db, run.id, hh.id, name="Salt")
    await _seed_item(db, run.id, hh.id, name="Milk")
    await _seed_item(db, run.id, hh.id, name="Atta")

    removed = await svc.remove_items_by_index(db, run.id, [0, 2])

    assert len(removed) == 2
    removed_names = {i.item_name for i in removed}
    assert "Salt" in removed_names
    assert "Atta" in removed_names

    from sqlalchemy import select
    from app.models.db import LoopRunItem
    remaining = (await db.execute(select(LoopRunItem).where(LoopRunItem.loop_run_id == run.id))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].item_name == "Milk"


@pytest.mark.asyncio
async def test_remove_by_index_out_of_range_ignored(svc, db):
    """Index beyond list length → silently ignored, no crash."""
    hh, _ = await _seed_household(db)
    run   = await _seed_loop_run(db, hh.id)
    await _seed_item(db, run.id, hh.id, name="Salt")

    removed = await svc.remove_items_by_index(db, run.id, [5, 99])

    assert removed == []

    from sqlalchemy import select
    from app.models.db import LoopRunItem
    remaining = (await db.execute(select(LoopRunItem).where(LoopRunItem.loop_run_id == run.id))).scalars().all()
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_remove_by_index_duplicate_indices_deduplicated(svc, db):
    """Duplicate indices → each item only removed once."""
    hh, _ = await _seed_household(db)
    run   = await _seed_loop_run(db, hh.id)
    await _seed_item(db, run.id, hh.id, name="Salt")

    removed = await svc.remove_items_by_index(db, run.id, [0, 0, 0])

    assert len(removed) == 1


@pytest.mark.asyncio
async def test_remove_by_index_empty_list_no_op(svc, db):
    """Empty indices list → nothing removed, no commit attempted."""
    hh, _ = await _seed_household(db)
    run   = await _seed_loop_run(db, hh.id)
    await _seed_item(db, run.id, hh.id)

    removed = await svc.remove_items_by_index(db, run.id, [])
    assert removed == []


# ══════════════════════════════════════════════════════════════════════════════
# add_item
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_add_item_from_mcp_product(svc, db):
    """add_item with MCPProduct creates LoopRunItem with added_by=user_added."""
    from app.mcp.types import MCPProduct
    hh, _ = await _seed_household(db)
    run   = await _seed_loop_run(db, hh.id)

    product = MCPProduct(
        sku_id   = "sku_amul_milk_002",
        name     = "Amul Toned Milk 1L",
        brand    = "Amul",
        price    = 64.0,
        mrp      = 64.0,
        in_stock = True,
    )

    new_item = await svc.add_item(db, run, hh.id, product, item_query="milk")

    assert new_item.item_name           == "milk"
    assert new_item.swiggy_product_name == "Amul Toned Milk 1L"
    assert new_item.swiggy_sku_id       == "sku_amul_milk_002"
    assert new_item.added_by            == "user_added"
    assert float(new_item.unit_price)   == 64.0
    assert float(new_item.total_price)  == 64.0
    assert new_item.id is not None


@pytest.mark.asyncio
async def test_add_item_from_dict_product(svc, db):
    """add_item also works when product is a plain dict (WhatsApp/mock flow)."""
    hh, _ = await _seed_household(db)
    run   = await _seed_loop_run(db, hh.id)

    product = {
        "sku_id":   "sku_ghee_007",
        "name":     "Amul Pure Ghee 500ml",
        "brand":    "Amul",
        "price":    295.0,
        "in_stock": True,
    }

    new_item = await svc.add_item(db, run, hh.id, product, item_query="ghee")

    assert new_item.swiggy_sku_id       == "sku_ghee_007"
    assert new_item.swiggy_product_name == "Amul Pure Ghee 500ml"
    assert new_item.added_by            == "user_added"


@pytest.mark.asyncio
async def test_add_item_query_takes_priority_as_item_name(svc, db):
    """item_query (user's search term) is stored as item_name, not product.name."""
    from app.mcp.types import MCPProduct
    hh, _ = await _seed_household(db)
    run   = await _seed_loop_run(db, hh.id)

    product = MCPProduct(
        sku_id="sku_x", name="Amul Toned Milk 1L", brand="Amul",
        price=64.0, mrp=64.0, in_stock=True,
    )
    new_item = await svc.add_item(db, run, hh.id, product, item_query="milk")

    # item_name is the user's query, product_name is the resolved SKU name
    assert new_item.item_name           == "milk"
    assert new_item.swiggy_product_name == "Amul Toned Milk 1L"


# ══════════════════════════════════════════════════════════════════════════════
# search_items
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_search_items_raises_token_expired(svc, db):
    """Expired/missing token → TokenExpiredError propagated."""
    hh, _ = await _seed_household(db)

    with patch(
        "app.services.auth_service.AuthService.get_valid_token",
        new=AsyncMock(side_effect=TokenExpiredError("Token expired")),
    ):
        with pytest.raises(TokenExpiredError):
            await svc.search_items(db, hh.id, "milk")


@pytest.mark.asyncio
async def test_search_items_raises_when_no_address(svc, db):
    """Household with no preferred_address_id → SwiggyMCPError raised."""
    from app.models.db import Household, SwiggyToken, HouseholdPreferences
    from app.utils.crypto import encrypt_token

    hh = Household(
        swiggy_user_id="swiggy_noaddr", household_type="single", member_count=1,
        diet_type="vegetarian", allergies=[], onboarding_complete=True,
        is_active=True, is_paused=False,
    )
    db.add(hh)
    await db.flush()
    db.add(SwiggyToken(
        household_id=hh.id,
        access_token_enc=encrypt_token("tok"),
        token_expiry=datetime.now(timezone.utc) + timedelta(days=7),
    ))
    db.add(HouseholdPreferences(
        household_id=hh.id,
        preferred_order_day="sunday",
        preferred_address_id=None,
    ))
    await db.commit()

    with patch(
        "app.services.auth_service.AuthService.get_valid_token",
        new=AsyncMock(return_value="fake_token"),
    ):
        with pytest.raises(SwiggyMCPError, match="No delivery address"):
            await svc.search_items(db, hh.id, "milk")


@pytest.mark.asyncio
async def test_search_items_filters_out_of_stock(svc, db):
    """Out-of-stock products are excluded from results."""
    from app.mcp.types import MCPProduct, MCPSearchResult
    hh, _ = await _seed_household(db)

    in_stock_product  = MCPProduct(sku_id="a", name="Salt", brand=None, price=28.0, mrp=28.0, in_stock=True)
    out_of_stock_product = MCPProduct(sku_id="b", name="Rare Item", brand=None, price=0.0, mrp=0.0, in_stock=False)
    mock_result = MCPSearchResult(products=[in_stock_product, out_of_stock_product], total_count=2, query="salt")

    mock_client = MagicMock()
    mock_client.search_products = AsyncMock(return_value=mock_result)

    with (
        patch("app.services.auth_service.AuthService.get_valid_token", new=AsyncMock(return_value="tok")),
        patch("app.providers.factory.get_mcp_provider", return_value=mock_client),
    ):
        results = await svc.search_items(db, hh.id, "salt")

    assert len(results) == 1
    assert results[0].name == "Salt"


@pytest.mark.asyncio
async def test_search_items_passes_swiggy_address_id(svc, db):
    """search_products is called with Swiggy's string address ID, not our UUID."""
    from app.mcp.types import MCPSearchResult
    hh, addr = await _seed_household(db)

    mock_client = MagicMock()
    mock_client.search_products = AsyncMock(return_value=MCPSearchResult(products=[], total_count=0, query="milk"))

    with (
        patch("app.services.auth_service.AuthService.get_valid_token", new=AsyncMock(return_value="tok")),
        patch("app.providers.factory.get_mcp_provider", return_value=mock_client),
    ):
        await svc.search_items(db, hh.id, "milk")

    # Must be called with swiggy_address_id ("addr_home_001"), not our UUID
    call_args = mock_client.search_products.call_args
    assert call_args.args[1] == "addr_home_001"
    assert call_args.args[1] != str(addr.id)


@pytest.mark.asyncio
async def test_search_items_returns_products_on_success(svc, db):
    """Happy path: returns filtered MCPProduct list."""
    from app.mcp.types import MCPProduct, MCPSearchResult
    hh, _ = await _seed_household(db)

    products = [
        MCPProduct(sku_id="a", name="Amul Milk", brand="Amul", price=64.0, mrp=64.0, in_stock=True),
        MCPProduct(sku_id="b", name="Mother Dairy Milk", brand="Mother Dairy", price=62.0, mrp=62.0, in_stock=True),
    ]
    mock_client = MagicMock()
    mock_client.search_products = AsyncMock(return_value=MCPSearchResult(products=products, total_count=2, query="milk"))

    with (
        patch("app.services.auth_service.AuthService.get_valid_token", new=AsyncMock(return_value="tok")),
        patch("app.providers.factory.get_mcp_provider", return_value=mock_client),
    ):
        results = await svc.search_items(db, hh.id, "milk", limit=5)

    assert len(results) == 2
    assert results[0].name == "Amul Milk"
    mock_client.search_products.assert_called_once_with("milk", "addr_home_001", limit=5)


# ══════════════════════════════════════════════════════════════════════════════
# _pattr helper
# ══════════════════════════════════════════════════════════════════════════════

def test_pattr_reads_from_dict():
    assert _pattr({"key": "val"}, "key") == "val"


def test_pattr_reads_from_object():
    class Obj:
        key = "obj_val"
    assert _pattr(Obj(), "key") == "obj_val"


def test_pattr_returns_default_when_missing():
    assert _pattr({}, "missing", "default") == "default"
    assert _pattr(object(), "missing", 99) == 99
