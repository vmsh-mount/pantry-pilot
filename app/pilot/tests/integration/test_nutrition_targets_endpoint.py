"""
Integration tests — Gap-to-Cart Phase A targets-UX layer
(GET /v1/nutrition/targets, PATCH /v1/settings nutrition_gaps_enabled)

Covers the PRD's Definition of Done:
  1. per_member_targets is public; reconciliation — summing per-member daily
     x7 equals personalised_weekly_targets's household total exactly.
  2. GET /v1/nutrition/targets per-member rows sum to the household total
     in the same response.
  3. A member with missing biometrics shows fallback_used: true.
  4. nutrition_gaps_enabled round-trips through PATCH/GET /v1/settings.
"""

import uuid

import pytest

from tests.integration.conftest import create_household, set_session


async def _add_member(db, household_id: str, **kwargs) -> str:
    from app.models.db import HouseholdMember
    m = HouseholdMember(id=str(uuid.uuid4()), household_id=household_id, **kwargs)
    db.add(m)
    await db.flush()
    await db.commit()
    return m.id


# ── 1. Reconciliation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_per_member_targets_sum_reconciles_with_household_total(db):
    from app.models.db import HouseholdMember
    from app.utils.nutrition_targets import per_member_targets, personalised_weekly_targets
    from sqlalchemy import select

    household_id = await create_household(db)
    await _add_member(db, household_id, role="adult", age_years=38, sex="male",
                       weight_kg=75, height_cm=175, activity_level="very_active")
    await _add_member(db, household_id, role="adult", age_years=35, sex="female",
                       weight_kg=60, height_cm=160, activity_level="sedentary")
    await _add_member(db, household_id, role="child", age_years=8, sex="male")

    members = (await db.execute(
        select(HouseholdMember).where(HouseholdMember.household_id == household_id)
    )).scalars().all()

    household_total = personalised_weekly_targets(members, 3)

    summed = {"calories": 0.0, "protein_g": 0.0, "fiber_g": 0.0, "sodium_mg": 0.0}
    for m in members:
        d = per_member_targets(m)
        for k in summed:
            summed[k] += d[k]
    reconciled = {
        "calories":  round(summed["calories"] * 7),
        "protein_g": round(summed["protein_g"] * 7, 1),
        "fiber_g":   round(summed["fiber_g"] * 7, 1),
        "sodium_mg": round(summed["sodium_mg"] * 7),
    }

    assert household_total == reconciled


# ── 2 & 3. Endpoint: per-member rows sum to household total; fallback flag ──

@pytest.mark.asyncio
async def test_targets_endpoint_per_member_sums_to_household_total(app_client, db):
    household_id = await create_household(db)
    await _add_member(db, household_id, role="adult", age_years=40, sex="male",
                       weight_kg=70, height_cm=175, activity_level="moderately_active")
    await _add_member(db, household_id, role="adult", age_years=38, sex="female")  # missing biometrics

    set_session(app_client, household_id)
    resp = await app_client.get("/v1/nutrition/targets")

    assert resp.status_code == 200
    data = resp.json()["data"]

    assert len(data["per_member"]) == 2
    summed_weekly_calories = sum(row["daily"]["calories"] for row in data["per_member"]) * 7
    assert summed_weekly_calories == data["household"]["weekly"]["calories"]

    # Member 1 has full biometrics -> not a fallback; member 2 is missing
    # weight/height -> fallback_used must be True, and must not be silently
    # blended in as personalized.
    rows_by_fallback = {row["fallback_used"] for row in data["per_member"]}
    assert True in rows_by_fallback
    assert data["source"] == "role_fallback"


@pytest.mark.asyncio
async def test_targets_endpoint_all_full_data_is_personalized_source(app_client, db):
    # create_household() defaults member_count=2 — must add a HouseholdMember
    # row for EACH slot with full biometrics, or the unmapped-slot check
    # correctly flags the household total as using a fallback default for
    # the slot with no row at all (see get_nutrition_targets's
    # `len(members) < member_count` check).
    household_id = await create_household(db)
    await _add_member(db, household_id, role="adult", age_years=30, sex="male",
                       weight_kg=70, height_cm=175, activity_level="sedentary")
    await _add_member(db, household_id, role="adult", age_years=28, sex="female",
                       weight_kg=58, height_cm=162, activity_level="lightly_active")

    set_session(app_client, household_id)
    resp = await app_client.get("/v1/nutrition/targets")

    data = resp.json()["data"]
    assert data["source"] == "personalized"
    assert all(row["fallback_used"] is False for row in data["per_member"])


@pytest.mark.asyncio
async def test_targets_endpoint_unauthenticated(app_client):
    resp = await app_client.get("/v1/nutrition/targets")
    body = resp.json()
    assert body["success"] is False


# ── 4. nutrition_gaps_enabled round-trips through settings ───────────────────

@pytest.mark.asyncio
async def test_nutrition_gaps_enabled_roundtrips_through_settings(app_client, db):
    household_id = await create_household(db)
    set_session(app_client, household_id)

    get_before = await app_client.get("/v1/settings")
    assert get_before.json()["data"]["nutrition_gaps_enabled"] is False

    patch_resp = await app_client.patch("/v1/settings", json={"nutrition_gaps_enabled": True})
    assert patch_resp.json()["data"]["nutrition_gaps_enabled"] is True

    get_after = await app_client.get("/v1/settings")
    assert get_after.json()["data"]["nutrition_gaps_enabled"] is True
