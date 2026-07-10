from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.schemas.common import APIResponse
from app.models.db import Household

router = APIRouter(prefix="/settings", tags=["settings"])


def _get_household_id(request: Request) -> str:
    hid = request.session.get("household_id")
    if not hid:
        raise ValueError("Not authenticated")
    return hid


@router.patch("", response_model=APIResponse)
async def update_settings_view(request: Request, body: dict, db: AsyncSession = Depends(get_db)):
    """Patch household settings — only provided fields are changed."""
    from app.services.household_service import HouseholdService
    household_id = _get_household_id(request)
    data = await HouseholdService(db).update_settings(household_id, body)
    return APIResponse.ok(data)


@router.get("", response_model=APIResponse)
async def get_settings_view(request: Request, db: AsyncSession = Depends(get_db)):
    """Return current household settings."""
    from app.services.household_service import HouseholdService
    household_id = _get_household_id(request)
    data = await HouseholdService(db).get_settings(household_id)
    return APIResponse.ok(data)


@router.post("/pause", response_model=APIResponse)
async def pause(request: Request, body: dict, db: AsyncSession = Depends(get_db)):
    """Pause the planning loop."""
    from app.services.household_service import HouseholdService
    household_id = _get_household_id(request)
    await HouseholdService(db).pause(household_id, reason=body.get("reason", ""))
    return APIResponse.ok({"paused": True})


@router.post("/resume", response_model=APIResponse)
async def resume(request: Request, db: AsyncSession = Depends(get_db)):
    """Resume a paused planning loop."""
    from app.services.household_service import HouseholdService
    household_id = _get_household_id(request)
    await HouseholdService(db).resume(household_id)
    return APIResponse.ok({"resumed": True})


class DietTypeUpdate(BaseModel):
    diet_type: str  # "vegetarian", "vegan", "non-vegetarian", "eggetarian"


class LifecycleSignalBody(BaseModel):
    event_type: str  # "diet_change" | "new_member" | "vacation_return" | "major_restock"
    new_member_count: int | None = None


@router.patch("/diet-type", response_model=APIResponse)
async def update_diet_type(body: DietTypeUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    """Update household diet type and emit a lifecycle signal."""
    household_id = _get_household_id(request)

    result = await db.execute(select(Household).where(Household.id == household_id))
    household = result.scalar_one_or_none()
    if not household:
        return APIResponse.fail("NOT_FOUND", "Household not found.")

    household.diet_type = body.diet_type
    await db.commit()

    from app.services.household_model_service import handle_lifecycle_event
    await handle_lifecycle_event(household_id, "diet_change", db)

    return APIResponse.ok({"status": "ok", "diet_type": body.diet_type})


@router.post("/lifecycle-signal", response_model=APIResponse)
async def lifecycle_signal(body: LifecycleSignalBody, request: Request, db: AsyncSession = Depends(get_db)):
    """Emit a lifecycle signal to update the household model."""
    household_id = _get_household_id(request)

    if body.event_type == "new_member" and body.new_member_count is not None:
        result = await db.execute(select(Household).where(Household.id == household_id))
        household = result.scalar_one_or_none()
        if not household:
            return APIResponse.fail("NOT_FOUND", "Household not found.")
        household.member_count = body.new_member_count
        await db.commit()

    from app.services.household_model_service import handle_lifecycle_event
    await handle_lifecycle_event(household_id, body.event_type, db)

    return APIResponse.ok({"status": "ok", "event_type": body.event_type})


@router.delete("/account", response_model=APIResponse)
async def delete_account(request: Request, db: AsyncSession = Depends(get_db)):
    """Delete household account and all data."""
    from app.services.household_service import HouseholdService
    from app.services.auth_service import AuthService
    household_id = _get_household_id(request)
    await AuthService(db).revoke_session(household_id)
    await HouseholdService(db).delete(household_id)
    request.session.clear()
    return APIResponse.ok({"deleted": True})
