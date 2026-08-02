"""
Integration test fixtures.

Design:
  - SWIGGY_RESPONSES defines the exact JSON-RPC response structure Swiggy MCP
    returns for each tool — captured once from the real API, frozen here.
  - `swiggy_mcp` fixture (session-scoped) patches httpx.AsyncClient.post so
    every test in this package gets the same fake Swiggy without hitting the
    network. It dispatches by tool name, mirroring what the real MCP does.
  - `db` fixture spins up an isolated Postgres schema per test (against the
    real postgres service) and tears it down afterwards, giving each test a
    clean slate without a separate container.
  - `app_client` provides an httpx.AsyncClient wired to the FastAPI app so
    tests can drive the full HTTP stack (auth → onboard → basket → orders).
  - `_reset_production_engine_pool` (autouse) disposes app.database.engine's
    pool before every test — anything that bypasses the get_db() DI override
    (background tasks, Celery task bodies called directly) still touches that
    process-wide singleton, and pytest-asyncio's per-test event loops make
    stale pooled connections a real, order-dependent failure mode otherwise.
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
# Un-poison app.database's production singleton (autouse)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    """
    Overrides (shadows, same fixture name) the root tests/conftest.py's
    autouse `mock_settings`, which monkeypatches app.config.get_settings to
    return a fake Settings with database_url pointing at "localhost".

    That's fine for unit tests (everything is mocked anyway), but it silently
    breaks integration tests: app.database's module-level `engine` is created
    lazily, at whichever moment app.database is FIRST imported anywhere in
    the whole pytest process — and nothing in this codebase imports it at
    collection time (it's always a deferred `from app.database import ...`
    inside a function body, the prevailing style here). If that first import
    happens to fire while some unit test's mock_settings patch is active,
    "localhost" gets baked into the engine permanently — real Postgres schema
    isolation and this suite's own real-DB `db` fixture below both still
    work fine (they never go through app.database's engine), but ANYTHING
    that bypasses the get_db() DI override and opens a session directly via
    app.database.AsyncSessionLocal (background tasks like process_signals,
    Celery task bodies invoked directly in a test) fails with a bare
    connection error that looks like a real infra problem, not what it
    actually is: a poisoned module singleton from cross-test/cross-suite
    import-order interference.

    Unlike the root fixture, this doesn't fabricate a MagicMock settings
    object or monkeypatch function references module-by-module (which only
    reaches code that imported get_settings AFTER the patch was installed —
    a whack-a-mole that broke again the moment providers/factory.py's own
    `from app.config import get_settings` turned out to need the same
    treatment as app.mcp.swiggy's). Instead: set the two env vars that
    actually need overriding for this suite, then clear get_settings's
    lru_cache. Every module gets the override this way regardless of how or
    when it imported get_settings, because they all end up calling the same
    underlying function, which now reads fresh from the (patched) env on its
    next call from anywhere.

      - PANTRYPILOT_DRY_RUN: real .env has this =true for local dev
        convenience, which would silently skip the real checkout path these
        tests intercept via the swiggy_mcp fixture and assert against
        (e.g. a specific swiggy_order_id).
      - SWIGGY_MCP_MODE: real .env has this =demo (set for the
        SWIGGY_MCP_MODE=demo recording work) — DemoSwiggyMCPClient never
        makes an HTTP call at all, so swiggy_mcp's transport-level mock
        never gets a chance to apply.

    database_url is deliberately left alone — the real value already points
    at the `postgres` hostname the `db` fixture below assumes; it's only the
    *unit* suite's MagicMock override (pointing at "localhost") that's the
    problem, and that never applies here in the first place.
    """
    monkeypatch.setenv("PANTRYPILOT_DRY_RUN", "false")
    monkeypatch.setenv("SWIGGY_MCP_MODE", "live")
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def _test_schema():
    """The per-test isolated schema name — a plain fixture (not the
    connection to it) so both `db` and `_reset_production_engine_pool` can
    share the same value without either owning the other's setup/teardown."""
    import uuid
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest_asyncio.fixture(autouse=True)
async def _reset_production_engine_pool(_test_schema):
    """
    Two problems, one fixture:

    1. Even with the URL fixed above, app.database.engine's pool is a
       singleton shared across every test in this process, while
       pytest-asyncio (asyncio_mode=auto, no explicit loop scope configured)
       gives each test function its own event loop. asyncpg connections are
       bound to the loop that created them, so a connection opened by one
       test's pool checkout is invalid in the next test's loop. Disposing
       before each test forces a fresh connection bound to that test's own
       loop — same fix applied in tasks/nutrition.py for the equivalent
       production issue.

    2. Once connected to the right database, code that bypasses the get_db()
       DI override (process_signals and anything else opening a session
       directly via app.database.AsyncSessionLocal) still lands in the
       default "public" schema, not the isolated per-test schema the `db`
       fixture below creates — so a row inserted through the DI-injected
       session (e.g. a household created via `db`) is invisible to a
       foreign-key check run through the production engine's connection,
       surfacing as a spurious "not present in table households" error that
       has nothing to do with the actual insert being wrong. A `connect`
       listener that sets search_path on every new connection this engine
       opens keeps both paths pointed at the same schema for the duration
       of the test.
    """
    from sqlalchemy import event
    from app.database import engine
    await engine.dispose()

    def _set_search_path(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute(f'SET search_path TO "{_test_schema}"')
        cursor.close()

    event.listen(engine.sync_engine, "connect", _set_search_path)
    try:
        yield
    finally:
        event.remove(engine.sync_engine, "connect", _set_search_path)
        await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# Per-test DB (real Postgres, isolated schema per test)
# ══════════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def db(_test_schema):
    """
    Each test gets a fresh PostgreSQL schema, runs all migrations, yields an
    AsyncSession, then drops the schema — giving complete isolation without
    spinning up a separate container.

    Requires the postgres service from docker-compose to be running, which it
    always is inside the pilot container (DATABASE_URL points at it).
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text
    from app.models.db import Base

    schema = _test_schema

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


async def enable_nutrition_gaps(db, household_id: str) -> None:
    """
    create_household() deliberately leaves nutrition_gaps_enabled at its
    column default (NULL/falsy) — test_nutrition_gaps_enabled_roundtrips_
    through_settings specifically asserts that default. Tests that exercise
    /v1/nutrition/{targets,weekly,gaps} need the flag on (see nutrition.py's
    _require_gap_to_cart_enabled server-side gate) — call this explicitly
    rather than changing the shared helper's default for everyone.
    """
    from app.models.db import Household
    hh = await db.get(Household, household_id)
    hh.nutrition_gaps_enabled = True
    await db.commit()


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
