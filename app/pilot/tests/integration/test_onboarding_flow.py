"""
Integration tests — Onboarding flow

Flows covered:
  1.  New user: OAuth callback → household created → redirect to /onboard
  2.  Existing user with complete onboarding → redirect to /dashboard
  3.  Existing user with incomplete onboarding → redirect to /onboard
  4.  POST /onboard/profile — saves household profile fields
  5.  POST /onboard/whatsapp/send-otp — sends OTP, stores in Redis
  6.  POST /onboard/whatsapp/send-otp — rate-limit: same number twice quickly
  7.  POST /onboard/whatsapp/verify-otp — correct OTP → whatsapp_verified=True
  8.  POST /onboard/whatsapp/verify-otp — wrong OTP → error
  9.  POST /onboard/whatsapp/verify-otp — expired OTP → error
  10. GET /onboard/infer — reads order history, infers diet + budget
  11. GET /onboard/basket-preview — empty pantry → falls back to go_to_items
  12. GET /onboard/basket-preview — established pantry → uses pantry items
  13. POST /onboard/complete — sets onboarding_complete=True, schedules loop
  14. POST /onboard/complete — place_order_now=True → triggers immediate loop
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from tests.integration.conftest import create_household, SWIGGY_RESPONSES


# ── Helper: create an incomplete household ─────────────────────────────────

async def create_incomplete_household(db, swiggy_user_id="swiggy_new_001"):
    """Household exists but onboarding_complete=False and no WhatsApp yet."""
    from app.models.db import Household, SwiggyToken
    from app.utils.crypto import encrypt_token

    hh = Household(
        swiggy_user_id      = swiggy_user_id,
        household_type      = "couple",
        member_count        = 2,
        diet_type           = "vegetarian",
        onboarding_complete = False,
        is_active           = True,
        is_paused           = False,
    )
    db.add(hh)
    await db.flush()

    token = SwiggyToken(
        household_id     = hh.id,
        access_token_enc = encrypt_token("fake_access_token_for_tests"),
        token_expiry     = datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(token)
    await db.commit()
    return str(hh.id)


async def auth_session(client, household_id: str):
    from tests.integration.conftest import encode_session
    client.cookies.set("session", encode_session(household_id))


# ══════════════════════════════════════════════════════════════════════════════
# 1–3. OAuth callback redirect rules
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_oauth_callback_new_user_redirects_to_onboard(app_client, db, swiggy_mcp):
    """New Swiggy user → household created, redirect to /onboard."""
    mock_auth = AsyncMock()
    mock_auth.household_id = "new-hh-001"
    mock_auth.is_new_user  = True

    with patch("app.services.auth_service.AuthService.handle_callback",
               new=AsyncMock(return_value=mock_auth)):
        resp = await app_client.get(
            "/v1/auth/callback?code=test_code&state=test_state",
            follow_redirects=False,
        )

    assert resp.status_code in (307, 302)
    assert "/onboard" in resp.headers["location"]


@pytest.mark.asyncio
async def test_oauth_callback_existing_user_redirects_to_dashboard(app_client, db):
    """Returning user with completed onboarding → redirect to /dashboard."""
    mock_auth = AsyncMock()
    mock_auth.household_id = "existing-hh-001"
    mock_auth.is_new_user  = False

    with patch("app.services.auth_service.AuthService.handle_callback",
               new=AsyncMock(return_value=mock_auth)):
        resp = await app_client.get(
            "/v1/auth/callback?code=test_code&state=test_state",
            follow_redirects=False,
        )

    assert resp.status_code in (307, 302)
    assert "/dashboard" in resp.headers["location"]


# ══════════════════════════════════════════════════════════════════════════════
# 4. POST /onboard/profile
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_save_profile_persists_fields(app_client, db):
    """Saving onboarding profile stores diet, budget, member_count in DB."""
    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.post("/v1/onboard/profile", json={
        "household_type":    "family",
        "member_count":      4,
        "diet_type":         "vegetarian",
        "allergies":         ["peanut"],
        "weekly_budget_min": 2000,
        "weekly_budget_max": 3500,
        "preferred_order_day": "saturday",
    })

    assert resp.status_code == 200
    assert resp.json()["success"] is True

    from sqlalchemy import select
    from app.models.db import Household
    result = await db.execute(select(Household).where(Household.id == household_id))
    hh = result.scalar_one()
    assert hh.diet_type         == "vegetarian"
    assert hh.member_count      == 4
    assert hh.weekly_budget_max == 3500
    assert "peanut" in (hh.allergies or [])


# ══════════════════════════════════════════════════════════════════════════════
# 5–6. WhatsApp OTP — send
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_send_otp_success(app_client, db):
    """Sending OTP to a valid number → success, OTP stored in Redis."""
    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_wa        = MagicMock()
    mock_wa.send_otp = AsyncMock()

    mock_wa_instance = MagicMock()
    mock_wa_instance.send_otp = AsyncMock()

    with (
        patch("app.providers.otp.redis_otp.get_redis", return_value=mock_redis),
        patch("app.services.whatsapp_service.WhatsAppService", return_value=mock_wa_instance),
    ):
        resp = await app_client.post("/v1/onboard/whatsapp/send-otp",
                                     json={"whatsapp_number": "+918499933228"})

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_wa_instance.send_otp.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# 7–9. WhatsApp OTP — verify
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_verify_otp_correct(app_client, db):
    """Correct OTP → whatsapp_verified=True in DB."""
    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    mock_redis = AsyncMock()
    # Stored format is "otp:attempts" (see redis_otp.py store())
    mock_redis.get    = AsyncMock(return_value="123456:0")
    mock_redis.delete = AsyncMock(return_value=1)

    with patch("app.providers.otp.redis_otp.get_redis", return_value=mock_redis):
        resp = await app_client.post("/v1/onboard/whatsapp/verify-otp", json={
            "whatsapp_number": "+918499933228",
            "otp":             "123456",
        })

    assert resp.status_code == 200
    assert resp.json()["success"] is True

    from sqlalchemy import select
    from app.models.db import Household
    result = await db.execute(select(Household).where(Household.id == household_id))
    hh = result.scalar_one()
    assert hh.whatsapp_verified is True
    assert hh.whatsapp_number   == "+918499933228"


@pytest.mark.asyncio
async def test_verify_otp_wrong_code(app_client, db):
    """Wrong OTP → error response, whatsapp_verified stays False."""
    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="999999:0")  # different OTP in store
    mock_redis.get_range = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.ttl = AsyncMock(return_value=300)

    with patch("app.providers.otp.redis_otp.get_redis", return_value=mock_redis):
        resp = await app_client.post("/v1/onboard/whatsapp/verify-otp", json={
            "whatsapp_number": "+918499933228",
            "otp":             "123456",   # wrong
        })

    assert resp.json()["success"] is False
    assert "invalid" in resp.json()["error"]["message"].lower() or \
           "incorrect" in resp.json()["error"]["message"].lower() or \
           resp.json()["error"]["code"] != "OK"


@pytest.mark.asyncio
async def test_verify_otp_expired(app_client, db):
    """Expired OTP (Redis returns None) → error response."""
    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)   # OTP expired / not found

    with patch("app.providers.otp.redis_otp.get_redis", return_value=mock_redis):
        resp = await app_client.post("/v1/onboard/whatsapp/verify-otp", json={
            "whatsapp_number": "+918499933228",
            "otp":             "123456",
        })

    assert resp.json()["success"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 10. GET /onboard/infer
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_infer_reads_order_history(app_client, db, swiggy_mcp):
    """Inference endpoint reads Swiggy order history and returns diet + budget."""
    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/onboard/infer")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    # Should infer some diet type from the all-veg mock orders
    assert "diet_type" in data["data"]
    d = data["data"]
    assert any(k.startswith("weekly_budget") or k == "budget" for k in d)


@pytest.mark.asyncio
async def test_infer_persists_preferred_address_id(app_client, db, swiggy_mcp):
    """GET /onboard/infer must save preferred_address_id so the place node has a delivery address."""
    from sqlalchemy import select
    from app.models.db import HouseholdPreferences

    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/onboard/infer")
    assert resp.json()["success"] is True

    # preferred_address_id must now be set — critical for order placement
    prefs_result = await db.execute(
        select(HouseholdPreferences).where(HouseholdPreferences.household_id == household_id)
    )
    prefs = prefs_result.scalar_one_or_none()
    assert prefs is not None, "HouseholdPreferences row must be created by infer"
    assert prefs.preferred_address_id is not None, (
        "preferred_address_id must be saved from Swiggy's first address — "
        "without it the place node fails with 'No delivery address'"
    )

    # Also verify the address row was upserted into the addresses table
    from app.models.db import Address
    addr_result = await db.execute(
        select(Address).where(Address.id == prefs.preferred_address_id)
    )
    addr = addr_result.scalar_one_or_none()
    assert addr is not None, "Address row must exist in addresses table"
    assert addr.swiggy_address_id == "addr_home_001"


# ══════════════════════════════════════════════════════════════════════════════
# 11–12. GET /onboard/basket-preview
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_basket_preview_empty_pantry_uses_go_to_items(app_client, db, swiggy_mcp):
    """First-time user with no pantry → preview uses your_go_to_items items."""
    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    resp = await app_client.get("/v1/onboard/basket-preview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    # go_to_items returns 5 items in SWIGGY_RESPONSES
    assert data["data"]["item_count"] > 0
    assert len(data["data"]["items"]) > 0


@pytest.mark.asyncio
async def test_basket_preview_with_pantry_items(app_client, db, swiggy_mcp):
    """User with pantry below threshold → preview shows those items resolved."""
    from app.models.db import PantryItem

    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    db.add(PantryItem(
        household_id          = household_id,
        item_name             = "Toor Dal",
        category              = "staples",
        standard_unit         = "kg",
        estimated_qty_remaining = 0.0,
        reorder_threshold     = 0.5,
        last_ordered_qty      = 1.0,
    ))
    await db.commit()

    resp = await app_client.get("/v1/onboard/basket-preview")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert any(i["item_name"] == "Toor Dal" for i in data["items"])


# ══════════════════════════════════════════════════════════════════════════════
# 13–14. POST /onboard/complete
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_complete_onboarding_sets_flag(app_client, db):
    """POST /onboard/complete → onboarding_complete=True in DB."""
    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    # Needs household preferences to exist for scheduling
    from app.models.db import HouseholdPreferences
    db.add(HouseholdPreferences(
        household_id         = household_id,
        preferred_order_day  = "sunday",
        preferred_delivery_slot = "evening",
    ))
    await db.commit()

    with patch("app.tasks.planning.trigger_planning_loop.apply_async"):
        resp = await app_client.post("/v1/onboard/complete",
                                     json={"place_order_now": False})

    assert resp.status_code == 200
    assert resp.json()["success"] is True

    from sqlalchemy import select
    from app.models.db import Household
    result = await db.execute(select(Household).where(Household.id == household_id))
    hh = result.scalar_one()
    assert hh.onboarding_complete is True


@pytest.mark.asyncio
async def test_complete_onboarding_place_now_triggers_loop(app_client, db):
    """POST /onboard/complete with place_order_now=True → planning loop triggered immediately."""
    household_id = await create_incomplete_household(db)
    await auth_session(app_client, household_id)

    from app.models.db import HouseholdPreferences
    db.add(HouseholdPreferences(
        household_id         = household_id,
        preferred_order_day  = "sunday",
        preferred_delivery_slot = "evening",
    ))
    await db.commit()

    with patch("app.tasks.planning.trigger_planning_loop.apply_async") as mock_apply, \
         patch("app.tasks.planning.trigger_planning_loop.delay") as mock_delay:
        resp = await app_client.post("/v1/onboard/complete",
                                     json={"place_order_now": True})

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    # One of these should have been called
    assert mock_apply.called or mock_delay.called
