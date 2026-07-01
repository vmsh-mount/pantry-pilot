from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.common import APIResponse

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
