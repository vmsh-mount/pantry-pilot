from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.common import APIResponse
from app.schemas.routines import RoutineCreate, RoutinePatch
from app.services.routines_service import RoutinesService

router = APIRouter(prefix="/routines", tags=["routines"])


def _hid(request: Request) -> str | None:
    return request.session.get("household_id")


@router.get("", response_model=APIResponse)
async def list_routines(request: Request, db: AsyncSession = Depends(get_db)):
    hid = _hid(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    routines = await RoutinesService(db).list_routines(hid)
    return APIResponse.ok([r.model_dump() for r in routines])


@router.post("", response_model=APIResponse)
async def create_routine(request: Request, body: RoutineCreate, db: AsyncSession = Depends(get_db)):
    hid = _hid(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    try:
        routine = await RoutinesService(db).create(hid, body)
        return APIResponse.ok(routine.model_dump())
    except ValueError as e:
        return APIResponse.fail("VALIDATION_ERROR", str(e))


@router.get("/{routine_id}", response_model=APIResponse)
async def get_routine(routine_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    hid = _hid(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    routine = await RoutinesService(db).get(routine_id, hid)
    if not routine:
        return APIResponse.fail("NOT_FOUND", "Routine not found.")
    return APIResponse.ok(routine.model_dump())


@router.patch("/{routine_id}", response_model=APIResponse)
async def patch_routine(routine_id: str, request: Request, body: RoutinePatch, db: AsyncSession = Depends(get_db)):
    hid = _hid(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    routine = await RoutinesService(db).patch(routine_id, hid, body)
    if not routine:
        return APIResponse.fail("NOT_FOUND", "Routine not found.")
    return APIResponse.ok({**routine.model_dump(), "message": "Changes saved — takes effect from the next run."})


@router.delete("/{routine_id}", response_model=APIResponse)
async def delete_routine(routine_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    hid = _hid(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    deleted = await RoutinesService(db).delete(routine_id, hid)
    if not deleted:
        return APIResponse.fail("NOT_FOUND", "Routine not found.")
    return APIResponse.ok({"deleted": True})


@router.post("/{routine_id}/pause", response_model=APIResponse)
async def pause_routine(routine_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    hid = _hid(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    routine = await RoutinesService(db).pause(routine_id, hid)
    if not routine:
        return APIResponse.fail("NOT_FOUND", "Routine not found or not active.")
    return APIResponse.ok(routine.model_dump())


@router.post("/{routine_id}/resume", response_model=APIResponse)
async def resume_routine(routine_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    hid = _hid(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    routine = await RoutinesService(db).resume(routine_id, hid)
    if not routine:
        return APIResponse.fail("NOT_FOUND", "Routine not found or not paused.")
    return APIResponse.ok(routine.model_dump())


@router.post("/{routine_id}/skip-next", response_model=APIResponse)
async def skip_next(routine_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    hid = _hid(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    routine = await RoutinesService(db).skip_next(routine_id, hid)
    if not routine:
        return APIResponse.fail("NOT_FOUND", "Routine not found or no upcoming run.")
    return APIResponse.ok(routine.model_dump())


@router.get("/{routine_id}/runs", response_model=APIResponse)
async def list_runs(routine_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    hid = _hid(request)
    if not hid:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")
    runs = await RoutinesService(db).list_runs(routine_id, hid)
    return APIResponse.ok([r.model_dump() for r in runs])
