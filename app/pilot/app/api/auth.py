from fastapi import APIRouter, Request, Response, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.common import APIResponse
from app.schemas.auth import AuthInitiateResponse, AuthCallbackResponse
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/initiate", response_model=APIResponse[AuthInitiateResponse])
async def initiate_oauth(request: Request, db: AsyncSession = Depends(get_db)):
    """Begin Swiggy OAuth 2.1 + PKCE flow. Returns redirect URL."""
    from app.services.auth_service import AuthService
    service = AuthService(db)
    session_id = request.session.get("session_id") or _new_session_id()
    redirect_url = await service.initiate_oauth(session_id)
    request.session["session_id"] = session_id
    return APIResponse.ok(AuthInitiateResponse(redirect_url=redirect_url))


@router.get("/callback")
async def handle_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Handle Swiggy OAuth redirect. Exchange code for token."""
    from app.services.auth_service import AuthService
    service = AuthService(db)
    session_id = request.session.get("session_id", "")

    from app.config import get_settings
    cockpit = get_settings().cockpit_url
    try:
        result = await service.handle_callback(code, state, session_id)
        request.session["household_id"] = result.household_id
        redirect_to = f"{cockpit}/onboard" if result.is_new_user else f"{cockpit}/dashboard"
        logger.info("oauth_callback_success", household_id=result.household_id, is_new=result.is_new_user)
        return RedirectResponse(url=redirect_to)
    except Exception as e:
        logger.error("oauth_callback_failed", error=str(e))
        return RedirectResponse(url=f"{cockpit}/?error={str(e)}")


@router.post("/logout", response_model=APIResponse[dict])
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    """Revoke Swiggy session and clear PantryPilot session."""
    from app.services.auth_service import AuthService
    household_id = request.session.get("household_id")
    if household_id:
        service = AuthService(db)
        await service.revoke_session(household_id)
    request.session.clear()
    return APIResponse.ok({"message": "Logged out successfully"})


@router.post("/reauth", response_model=APIResponse[AuthInitiateResponse])
async def reauth(request: Request, db: AsyncSession = Depends(get_db)):
    """Trigger re-authentication for an existing household (token expired)."""
    from app.services.auth_service import AuthService
    service = AuthService(db)
    session_id = request.session.get("session_id") or _new_session_id()
    redirect_url = await service.initiate_oauth(session_id)
    request.session["session_id"] = session_id
    return APIResponse.ok(AuthInitiateResponse(redirect_url=redirect_url))


def _new_session_id() -> str:
    import secrets
    return secrets.token_urlsafe(32)
