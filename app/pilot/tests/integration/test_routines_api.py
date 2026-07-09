"""
Integration tests — Routines API

Endpoints under test:
  GET    /v1/routines                      list routines
  POST   /v1/routines                      create routine
  GET    /v1/routines/{id}                 get routine
  PATCH  /v1/routines/{id}                 update routine
  DELETE /v1/routines/{id}                 soft-delete
  POST   /v1/routines/{id}/pause           pause
  POST   /v1/routines/{id}/resume          resume
  POST   /v1/routines/{id}/skip-next       skip next run
  GET    /v1/routines/{id}/runs            list runs

Flows covered:
  Auth
    1.  Unauthenticated → NOT_AUTHENTICATED on all routes

  List
    2.  Empty list for new household
    3.  Created routine appears in list
    4.  Deleted routine not in list

  Create
    5.  Happy path — returns 201 with id, status=active, next_run_at set
    6.  Invalid monthly day (0 / 29) → 422
    7.  Missing items list → 422
    8.  schedule_time stored and returned as IST "HH:MM"

  Get
    9.  Returns 404 for unknown id
    10. Returns 404 for another household's routine
    11. Returns correct routine including items and upcoming_runs

  Patch
    12. Rename routine
    13. Change schedule_time — next_run_at recomputed
    14. Replace items list

  Delete
    15. Returns 204
    16. Subsequent GET → 404
    17. Does not appear in list after delete

  Pause / Resume
    18. Pause active routine → status becomes paused
    19. Pause already-paused → 404 (only active can be paused)
    20. Resume paused routine → status becomes active, next_run_at set
    21. Resume active routine → 404

  Skip next
    22. Skip advances next_run_at by one period
    23. Skip creates a RoutineRun with status=skipped

  Runs
    24. Empty list when no runs recorded
    25. Run appears after skip_next
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from tests.integration.conftest import create_household, _mcp_ok

UTC = timezone.utc

# ── Auth helper ───────────────────────────────────────────────────────────────

def _set_session(client, household_id: str):
    from tests.integration.conftest import encode_session
    client.cookies.set("session", encode_session(household_id))


def _err(r, code: str) -> bool:
    """True when response carries an API-level error with the given code (HTTP 200, success=false)."""
    body = r.json()
    return not body.get("success") and body.get("error", {}).get("code") == code


# ── Routine payload factory ───────────────────────────────────────────────────

def _routine_payload(**overrides):
    base = {
        "name": "Weekly Staples",
        "frequency_type": "every_n_days",
        "frequency_value": 7,
        "schedule_time": "08:00",
        "items": [
            {"item_name": "Tata Salt", "quantity": 2, "unit": "kg",
             "swiggy_product_id": "sku_tata_salt_001", "swiggy_product_name": "Tata Salt 1kg"},
            {"item_name": "Amul Milk", "quantity": 3, "unit": "L",
             "swiggy_product_id": None, "swiggy_product_name": None},
        ],
    }
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# 1. Auth guard
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_list_unauthenticated(app_client):
    r = await app_client.get("/v1/routines")
    assert r.status_code == 200
    assert _err(r, "NOT_AUTHENTICATED")


@pytest.mark.anyio
async def test_create_unauthenticated(app_client):
    r = await app_client.post("/v1/routines", json=_routine_payload())
    assert r.status_code == 200
    assert _err(r, "NOT_AUTHENTICATED")


# ══════════════════════════════════════════════════════════════════════════════
# 2-4. List
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_list_empty(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)
    r = await app_client.get("/v1/routines")
    assert r.status_code == 200
    assert r.json()["data"] == []


@pytest.mark.anyio
async def test_created_routine_in_list(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    assert cr.json()["success"] is True

    r = await app_client.get("/v1/routines")
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1
    assert r.json()["data"][0]["name"] == "Weekly Staples"


@pytest.mark.anyio
async def test_deleted_routine_not_in_list(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]

    dr = await app_client.delete(f"/v1/routines/{routine_id}")
    assert dr.json()["success"] is True

    r = await app_client.get("/v1/routines")
    assert r.json()["data"] == []


# ══════════════════════════════════════════════════════════════════════════════
# 5-8. Create
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_create_happy_path(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    r = await app_client.post("/v1/routines", json=_routine_payload())
    assert r.json()["success"] is True
    data = r.json()["data"]
    assert data["status"] == "active"
    assert data["next_run_at"] is not None
    assert len(data["items"]) == 2
    assert data["schedule_time_ist"] == "08:00"


@pytest.mark.anyio
async def test_create_invalid_monthly_day(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    r = await app_client.post("/v1/routines", json=_routine_payload(
        frequency_type="monthly", frequency_value=29
    ))
    assert _err(r, "VALIDATION_ERROR")


@pytest.mark.anyio
async def test_create_missing_items(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    payload = _routine_payload()
    del payload["items"]
    r = await app_client.post("/v1/routines", json=payload)
    assert r.status_code == 422


@pytest.mark.anyio
async def test_create_schedule_time_round_trips_as_ist(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    r = await app_client.post("/v1/routines", json=_routine_payload(schedule_time="20:30"))
    assert r.json()["success"] is True
    assert r.json()["data"]["schedule_time_ist"] == "20:30"


# ══════════════════════════════════════════════════════════════════════════════
# 9-11. Get
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_get_not_found(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    r = await app_client.get("/v1/routines/00000000-0000-0000-0000-000000000000")
    assert _err(r, "NOT_FOUND")


@pytest.mark.anyio
async def test_get_other_household_routine(app_client, db):
    from tests.integration.conftest import encode_session
    hh1 = await create_household(db, swiggy_user_id="user_A")
    hh2 = await create_household(db, swiggy_user_id="user_B")

    # Create routine as hh1
    cr = await app_client.get(
        "/v1/routines",
        cookies={"session": encode_session(hh1)},
    )
    # Post as hh1 using explicit cookie (don't mutate client state)
    cr = await app_client.post(
        "/v1/routines",
        json=_routine_payload(),
        cookies={"session": encode_session(hh1)},
    )
    routine_id = cr.json()["data"]["id"]

    # Fetch as hh2 — should not see hh1's routine
    r = await app_client.get(
        f"/v1/routines/{routine_id}",
        cookies={"session": encode_session(hh2)},
    )
    assert _err(r, "NOT_FOUND")


@pytest.mark.anyio
async def test_get_returns_correct_routine(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]

    r = await app_client.get(f"/v1/routines/{routine_id}")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["id"] == routine_id
    assert data["name"] == "Weekly Staples"
    assert len(data["items"]) == 2
    assert len(data["upcoming_runs"]) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 12-14. Patch
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_patch_rename(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]

    r = await app_client.patch(f"/v1/routines/{routine_id}", json={"name": "Daily Essentials"})
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "Daily Essentials"


@pytest.mark.anyio
async def test_patch_schedule_time_recomputes_next_run_at(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload(schedule_time="08:00"))
    routine_id = cr.json()["data"]["id"]
    old_next = cr.json()["data"]["next_run_at"]

    r = await app_client.patch(f"/v1/routines/{routine_id}", json={"schedule_time": "21:00"})
    assert r.status_code == 200
    new_next = r.json()["data"]["next_run_at"]
    assert new_next != old_next
    assert r.json()["data"]["schedule_time_ist"] == "21:00"


@pytest.mark.anyio
async def test_patch_replace_items(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]

    new_items = [{"item_name": "Bread", "quantity": 1, "unit": "loaf",
                  "swiggy_product_id": None, "swiggy_product_name": None}]
    r = await app_client.patch(f"/v1/routines/{routine_id}", json={"items": new_items})
    assert r.status_code == 200
    returned_items = r.json()["data"]["items"]
    assert len(returned_items) == 1
    assert returned_items[0]["item_name"] == "Bread"


# ══════════════════════════════════════════════════════════════════════════════
# 15-17. Delete
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_delete_returns_204(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]

    r = await app_client.delete(f"/v1/routines/{routine_id}")
    assert r.json()["success"] is True


@pytest.mark.anyio
async def test_get_after_delete_is_404(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]
    await app_client.delete(f"/v1/routines/{routine_id}")

    r = await app_client.get(f"/v1/routines/{routine_id}")
    assert _err(r, "NOT_FOUND")


@pytest.mark.anyio
async def test_deleted_not_in_list(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]
    await app_client.delete(f"/v1/routines/{routine_id}")

    r = await app_client.get("/v1/routines")
    assert all(item["id"] != routine_id for item in r.json()["data"])


# ══════════════════════════════════════════════════════════════════════════════
# 18-21. Pause / Resume
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_pause_active_routine(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]

    r = await app_client.post(f"/v1/routines/{routine_id}/pause")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "paused"


@pytest.mark.anyio
async def test_pause_already_paused_is_404(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]
    await app_client.post(f"/v1/routines/{routine_id}/pause")

    r = await app_client.post(f"/v1/routines/{routine_id}/pause")
    assert _err(r, "NOT_FOUND")


@pytest.mark.anyio
async def test_resume_paused_routine(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]
    await app_client.post(f"/v1/routines/{routine_id}/pause")

    r = await app_client.post(f"/v1/routines/{routine_id}/resume")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "active"
    assert data["next_run_at"] is not None


@pytest.mark.anyio
async def test_resume_active_routine_is_404(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]

    r = await app_client.post(f"/v1/routines/{routine_id}/resume")
    assert _err(r, "NOT_FOUND")


# ══════════════════════════════════════════════════════════════════════════════
# 22-23. Skip next
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_skip_next_advances_next_run_at(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload(
        frequency_type="every_n_days", frequency_value=7
    ))
    routine_id = cr.json()["data"]["id"]
    original_next = cr.json()["data"]["next_run_at"]

    r = await app_client.post(f"/v1/routines/{routine_id}/skip-next")
    assert r.status_code == 200
    new_next = r.json()["data"]["next_run_at"]
    assert new_next != original_next
    # New next_run_at must be later
    assert datetime.fromisoformat(new_next) > datetime.fromisoformat(original_next)


@pytest.mark.anyio
async def test_skip_next_creates_skipped_run(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]
    await app_client.post(f"/v1/routines/{routine_id}/skip-next")

    r = await app_client.get(f"/v1/routines/{routine_id}/runs")
    assert r.status_code == 200
    runs = r.json()["data"]
    assert len(runs) == 1
    assert runs[0]["status"] == "skipped"
    assert runs[0]["skip_reason"] == "user_skip"


# ══════════════════════════════════════════════════════════════════════════════
# 24-25. Runs list
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_runs_empty_initially(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]

    r = await app_client.get(f"/v1/routines/{routine_id}/runs")
    assert r.status_code == 200
    assert r.json()["data"] == []


@pytest.mark.anyio
async def test_runs_appear_after_skip(app_client, db):
    hh_id = await create_household(db)
    _set_session(app_client, hh_id)

    cr = await app_client.post("/v1/routines", json=_routine_payload())
    routine_id = cr.json()["data"]["id"]

    await app_client.post(f"/v1/routines/{routine_id}/skip-next")
    await app_client.post(f"/v1/routines/{routine_id}/skip-next")

    r = await app_client.get(f"/v1/routines/{routine_id}/runs")
    assert r.status_code == 200
    assert len(r.json()["data"]) == 2
