"""
Integration test fixtures.

Design:
  - SWIGGY_RESPONSES defines the exact JSON-RPC response structure Swiggy MCP
    returns for each tool — captured once from the real API, frozen here.
  - `swiggy_mcp` fixture (session-scoped) patches httpx.AsyncClient.post so
    every test in this package gets the same fake Swiggy without hitting the
    network. It dispatches by tool name, mirroring what the real MCP does.
  - `db` fixture spins up a fresh in-memory SQLite database per test and tears
    it down afterwards, giving each test a clean slate.
  - `app_client` provides an httpx.AsyncClient wired to the FastAPI app so
    tests can drive the full HTTP stack (auth → onboard → basket → orders).
"""

import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, Response, ASGITransport


# ══════════════════════════════════════════════════════════════════════════════
# Frozen Swiggy MCP response structures (captured once from the real API)
# ══════════════════════════════════════════════════════════════════════════════

SWIGGY_RESPONSES = {
    # ── get_addresses ─────────────────────────────────────────────────────────
    "get_addresses": {
        "addresses": [
            {
                "id":              "addr_home_001",
                "addressLine":     "Flat 203, Sunrise Apartments, 5th Cross, Koramangala",
                "addressTag":      "Home",
                "addressCategory": "home",
                "phoneNumber":     "+918499933228",
                "lat":             12.9352,
                "lng":             77.6245,
            },
            {
                "id":              "addr_work_002",
                "addressLine":     "WeWork, Embassy Tech Village, Outer Ring Road",
                "addressTag":      "Work",
                "addressCategory": "work",
                "phoneNumber":     "+918499933228",
                "lat":             12.9698,
                "lng":             77.7499,
            },
        ]
    },

    # ── your_go_to_items ──────────────────────────────────────────────────────
    # Real Swiggy shape: top-level "products" key, same variations[] structure
    # as search_products. _parse_go_to_item reads spinId + price.offerPrice.
    "your_go_to_items": {
        "products": [
            {
                "productId":   "sku_tata_salt_001",
                "displayName": "Tata Salt",
                "brand":       "Tata",
                "inStock":     True,
                "variations": [{"spinId": "sku_tata_salt_001", "displayName": "Tata Salt 1kg",
                                 "brandName": "Tata", "quantityDescription": "1 kg",
                                 "isInStockAndAvailable": True,
                                 "price": {"mrp": 28.0, "offerPrice": 28.0}}],
            },
            {
                "productId":   "sku_amul_milk_002",
                "displayName": "Amul Toned Milk",
                "brand":       "Amul",
                "inStock":     True,
                "variations": [{"spinId": "sku_amul_milk_002", "displayName": "Amul Toned Milk 1L",
                                 "brandName": "Amul", "quantityDescription": "1 L",
                                 "isInStockAndAvailable": True,
                                 "price": {"mrp": 68.0, "offerPrice": 64.0}}],
            },
            {
                "productId":   "sku_atta_003",
                "displayName": "Aashirvaad Atta",
                "brand":       "Aashirvaad",
                "inStock":     True,
                "variations": [{"spinId": "sku_atta_003", "displayName": "Aashirvaad Atta 5kg",
                                 "brandName": "Aashirvaad", "quantityDescription": "5 kg",
                                 "isInStockAndAvailable": True,
                                 "price": {"mrp": 340.0, "offerPrice": 320.0}}],
            },
            {
                "productId":   "sku_toor_dal_004",
                "displayName": "Toor Dal",
                "brand":       "24 Mantra",
                "inStock":     True,
                "variations": [{"spinId": "sku_toor_dal_004", "displayName": "Toor Dal 1kg",
                                 "brandName": "24 Mantra", "quantityDescription": "1 kg",
                                 "isInStockAndAvailable": True,
                                 "price": {"mrp": 175.0, "offerPrice": 165.0}}],
            },
            {
                "productId":   "sku_tomato_005",
                "displayName": "Tomato",
                "brand":       None,
                "inStock":     True,
                "variations": [{"spinId": "sku_tomato_005", "displayName": "Tomato 500g",
                                 "brandName": None, "quantityDescription": "500 g",
                                 "isInStockAndAvailable": True,
                                 "price": {"mrp": 42.0, "offerPrice": 40.0}}],
            },
        ],
        "nextOffset": None,
    },

    # ── search_products ───────────────────────────────────────────────────────
    # Matches real Swiggy search_products shape: products with `variations[]`.
    # `_parse_product` in swiggy.py reads spinId from variations[0] for sku_id,
    # and price from variations[0].price.offerPrice.
    "search_products": {
        "products": [
            {
                "productId":   "sku_tata_salt_001",
                "displayName": "Tata Salt",
                "brand":       "Tata",
                "category":    "staples",
                "inStock":     True,
                "variations": [
                    {
                        "spinId":                "sku_tata_salt_001",
                        "displayName":           "Tata Salt 1kg",
                        "brandName":             "Tata",
                        "quantityDescription":   "1 kg",
                        "isInStockAndAvailable": True,
                        "price": {"mrp": 28.0, "offerPrice": 28.0},
                    }
                ],
            },
            {
                "productId":   "sku_amul_milk_002",
                "displayName": "Amul Toned Milk",
                "brand":       "Amul",
                "category":    "dairy",
                "inStock":     True,
                "variations": [
                    {
                        "spinId":                "sku_amul_milk_002",
                        "displayName":           "Amul Toned Milk 1L",
                        "brandName":             "Amul",
                        "quantityDescription":   "1 L",
                        "isInStockAndAvailable": True,
                        "price": {"mrp": 64.0, "offerPrice": 64.0},
                    }
                ],
            },
        ],
        "totalCount": 2,
    },

    # ── get_cart ──────────────────────────────────────────────────────────────
    "get_cart": {
        "items": [],
        "billDetails": {"itemTotal": 0.0, "deliveryFee": 0.0, "taxes": 0.0, "grandTotal": 0.0},
    },

    # ── update_cart ───────────────────────────────────────────────────────────
    # _parse_cart in swiggy.py reads flat keys: item_total, grand_total, etc.
    # Items must have "sku_id" (not itemId) per _parse_cart's i["sku_id"].
    "update_cart": {
        "items": [
            {"sku_id": "sku_tata_salt_001", "name": "Tata Salt", "quantity": 1,
             "unit_price": 28.0, "total_price": 28.0},
        ],
        "item_total":   28.0,
        "delivery_fee": 25.0,
        "taxes":         5.0,
        "grand_total":  520.0,
        "item_count":    1,
    },

    # ── clear_cart ────────────────────────────────────────────────────────────
    "clear_cart": {"success": True},

    # ── checkout ──────────────────────────────────────────────────────────────
    "checkout": {
        "orderId":           "241629385719397",
        "status":            "PLACED",
        "totalAmount":       520.0,
        "estimatedDelivery": "Today, 6–8 PM",
    },

    # ── get_orders ────────────────────────────────────────────────────────────
    "get_orders": {
        "orders": [
            {
                "orderId":     "241629385719397",
                "status":      "DELIVERED",
                "createdAt":   "2026-06-28T15:16:25.000Z",
                "updatedAt":   "2026-06-28T15:45:45.000Z",
                "totalAmount": 266.0,
                "itemCount":   4,
                "items": [
                    {"name": "Tata Salt",      "quantity": 2, "itemId": "sku_tata_salt_001"},
                    {"name": "Amul Toned Milk", "quantity": 3, "itemId": "sku_amul_milk_002"},
                ],
            },
            {
                "orderId":     "241513241502158",
                "status":      "DELIVERED",
                "createdAt":   "2026-06-21T10:00:00.000Z",
                "updatedAt":   "2026-06-21T10:30:00.000Z",
                "totalAmount": 271.0,
                "itemCount":   5,
                "items": [
                    {"name": "Aashirvaad Atta", "quantity": 1, "itemId": "sku_atta_003"},
                    {"name": "Toor Dal",         "quantity": 1, "itemId": "sku_toor_dal_004"},
                ],
            },
        ],
        "hasMore": False,
    },

    # ── get_order_details ─────────────────────────────────────────────────────
    "get_order_details": {
        "orderId":     "241629385719397",
        "status":      "DELIVERED",
        "createdAt":   "2026-06-28T15:16:25.000Z",
        "updatedAt":   "2026-06-28T15:45:45.000Z",
        "items": [
            {
                "itemId":     "sku_tata_salt_001",
                "name":       "Tata Salt",
                "brand":      "Tata",
                "quantity":   2,
                "unit":       "1kg",
                "unitPrice":  28.0,
                "totalPrice": 56.0,
            },
            {
                "itemId":     "sku_amul_milk_002",
                "name":       "Amul Toned Milk",
                "brand":      "Amul",
                "quantity":   3,
                "unit":       "1L",
                "unitPrice":  64.0,
                "totalPrice": 192.0,
            },
        ],
        "billDetails": {
            "itemTotal":  248.0,
            "deliveryFee": 0.0,
            "taxes":      18.0,
            "grandTotal": 266.0,
        },
        "deliveryAddress": {"id": "addr_home_001"},
    },
}


def _mcp_ok(tool_name: str, override: dict | None = None) -> Response:
    """Build a 200 JSON-RPC success response for the given tool."""
    data = dict(SWIGGY_RESPONSES.get(tool_name, {}))
    if override:
        data.update(override)
    body = {
        "jsonrpc": "2.0",
        "id":      1,
        "result":  {"structuredContent": data},
    }
    return Response(200, json=body)


def _mcp_error(code: int, message: str) -> Response:
    """Build a JSON-RPC error response."""
    return Response(code, json={"error": message})


# ══════════════════════════════════════════════════════════════════════════════
# Session-scoped Swiggy MCP mock
# Intercepts httpx.AsyncClient.post once for the whole test session.
# Individual tests can override per-call by passing a `mcp_overrides` dict.
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def swiggy_mcp():
    """
    Patches httpx.AsyncClient.post for the entire session.
    Dispatches by tool name extracted from the JSON-RPC body.
    Returns a dict you can mutate per-test to override specific tool responses.
    """
    overrides: dict[str, Response | None] = {}

    import httpx as _httpx
    _real_post = _httpx.AsyncClient.post  # capture before patching

    async def _fake_post(self, url, *, json=None, headers=None, **kwargs):
        # Only intercept MCP/Swiggy calls — identified by JSON-RPC structure.
        # Test-client calls to the FastAPI app (via ASGITransport) must pass through
        # using the real AsyncClient.post implementation.
        body = json or {}
        if body.get("jsonrpc") == "2.0" and "params" in body:
            tool_name = body.get("params", {}).get("name", "")
            if tool_name in overrides and overrides[tool_name] is not None:
                return overrides[tool_name]
            return _mcp_ok(tool_name)
        # Not an MCP call — let it through (ASGITransport handles it internally)
        return await _real_post(self, url, json=json, headers=headers, **kwargs)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        yield overrides   # tests can do: swiggy_mcp["search_products"] = _mcp_error(500, "...")

    # cleanup: reset overrides after session (defensive)
    overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# In-memory DB per test (SQLite via aiosqlite)
# ══════════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def db():
    """
    Each test gets a fresh PostgreSQL schema, runs all migrations, yields an
    AsyncSession, then drops the schema — giving complete isolation without
    spinning up a separate container.

    Requires the postgres service from docker-compose to be running, which it
    always is inside the pilot container (DATABASE_URL points at it).
    """
    import uuid
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text
    from app.models.db import Base

    # Each test uses a unique schema so tests can run in parallel without clobbering each other.
    schema = f"test_{uuid.uuid4().hex[:12]}"

    # Connect to the app's postgres (same host, different schema)
    base_url = "postgresql+asyncpg://pantrypilot:pantrypilot@postgres:5432/pantrypilot"
    engine = create_async_engine(base_url, echo=False)

    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        # Make SQLAlchemy create tables inside this schema
        await conn.execute(text(f'SET search_path TO "{schema}"'))
        await conn.run_sync(Base.metadata.create_all)

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        # Pin this session to the isolated schema
        await session.execute(text(f'SET search_path TO "{schema}"'))
        yield session

    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI test client (full HTTP stack, session cookie preserved)
# ══════════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def app_client(swiggy_mcp, db):
    """
    httpx.AsyncClient backed by the FastAPI ASGI app.
    - Swiggy MCP is already mocked by `swiggy_mcp`.
    - LLM and WhatsApp are mocked here (no external calls).
    - Uses the test DB session injected via dependency override.
    """
    from app.main import app
    from app.database import get_db

    # Override DB dependency so the app uses our test DB
    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db

    # Mock LLM — returns a valid empty basket JSON
    llm_json = json.dumps({"additions": [], "flags": [], "seasonal_note": None})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=llm_json)

    # Mock WhatsApp — swallow all sends silently
    mock_wa = MagicMock()
    mock_wa.send_text               = AsyncMock()
    mock_wa.send_basket_preview     = AsyncMock()
    mock_wa.send_order_confirmation = AsyncMock()

    with (
        patch("app.providers.factory.get_llm_provider",       return_value=mock_llm),
        patch("app.providers.factory.get_whatsapp_provider",  return_value=mock_wa),
        patch("app.services.whatsapp_service.WhatsAppService", return_value=mock_wa),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client

    app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════

async def create_household(db, swiggy_user_id: str = "swiggy_user_001") -> str:
    """Insert a minimal household record directly into the test DB. Returns household_id."""
    from app.models.db import Household, SwiggyToken, HouseholdPreferences
    from app.utils.crypto import encrypt_token
    import uuid
    from datetime import datetime, timezone, timedelta

    hh = Household(
        swiggy_user_id    = swiggy_user_id,
        household_type    = "couple",
        member_count      = 2,
        diet_type         = "vegetarian",
        allergies         = [],
        weekly_budget_min = 1500,
        weekly_budget_max = 2500,
        onboarding_complete = True,
        is_active         = True,
        is_paused         = False,
        whatsapp_number   = "+918499933228",
        whatsapp_verified = True,
        city              = "Bengaluru",
    )
    db.add(hh)
    await db.flush()

    token = SwiggyToken(
        household_id     = hh.id,
        access_token_enc = encrypt_token("fake_access_token_for_tests"),
        token_expiry     = datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(token)

    prefs = HouseholdPreferences(
        household_id          = hh.id,
        preferred_order_day   = "sunday",
        preferred_delivery_slot = "evening",
        preferred_address_id  = None,   # address IDs come from Swiggy MCP, not stored as UUIDs
    )
    db.add(prefs)

    await db.commit()
    return str(hh.id)


def set_session(client, household_id: str) -> None:
    """Inject household_id into the test client's session cookie (Starlette format)."""
    client.cookies.set("session", encode_session(household_id))


def encode_session(household_id: str) -> str:
    """
    Produce a session cookie value that Starlette's SessionMiddleware will accept.
    Starlette uses TimestampSigner(secret_key).sign(base64(json(data))).
    """
    import base64, json
    from itsdangerous import TimestampSigner
    from app.config import get_settings
    signer = TimestampSigner(get_settings().jwt_secret)
    payload = base64.b64encode(json.dumps({"household_id": household_id}).encode("utf-8"))
    return signer.sign(payload).decode("utf-8")
