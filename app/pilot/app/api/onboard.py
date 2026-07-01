from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.common import APIResponse
from app.schemas.onboard import (
    HouseholdProfileRequest, OTPRequest,
    OTPVerifyRequest, OnboardCompleteRequest
)

router = APIRouter(prefix="/onboard", tags=["onboarding"])


def _get_household_id(request: Request) -> str:
    hid = request.session.get("household_id")
    if not hid:
        raise ValueError("Not authenticated")
    return hid


@router.get("/status", response_model=APIResponse)
async def get_onboard_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Return current onboarding state so the frontend can resume at the right step."""
    from sqlalchemy import select
    from app.models.db import Household

    hid = _get_household_id(request)
    result = await db.execute(select(Household).where(Household.id == hid))
    hh = result.scalar_one_or_none()
    if not hh:
        return APIResponse.fail("NOT_FOUND", "Household not found.")

    return APIResponse.ok({
        "onboarding_complete": hh.onboarding_complete,
        "whatsapp_verified":   hh.whatsapp_verified,
        "whatsapp_number":     hh.whatsapp_number,
        "profile_saved":       hh.weekly_budget_max is not None,
        "diet_type":           hh.diet_type,
        "household_type":      hh.household_type,
        "member_count":        hh.member_count,
        "weekly_budget_min":   hh.weekly_budget_min,
        "weekly_budget_max":   hh.weekly_budget_max,
    })


@router.get("/infer", response_model=APIResponse)
async def run_inference(request: Request, db: AsyncSession = Depends(get_db)):
    """Pull Swiggy order history and run inference pass."""
    from app.services.onboarding_service import OnboardingService
    from app.services.auth_service import AuthService
    from app.models.db import Address, HouseholdPreferences
    from sqlalchemy import select
    household_id = _get_household_id(request)
    token = await AuthService(db).get_valid_token(household_id)
    result = await OnboardingService(db).run_inference(household_id, token)

    # Persist address so the place node has a delivery target later.
    # preferred_address_id is a FK to addresses.id (our UUID), not the Swiggy address ID,
    # so we must upsert the Swiggy address into our addresses table first.
    address_line = None
    if result.address_id:
        addr_result = await db.execute(
            select(Address).where(
                Address.household_id      == household_id,
                Address.swiggy_address_id == result.address_id,
            )
        )
        addr = addr_result.scalar_one_or_none()
        if addr is None:
            addr = Address(
                household_id      = household_id,
                swiggy_address_id = result.address_id,
                label             = result.address_label,
                is_default        = True,
            )
            db.add(addr)
            await db.flush()   # get addr.id

        if result.address_label:
            address_line = result.address_label

        prefs_result = await db.execute(
            select(HouseholdPreferences).where(HouseholdPreferences.household_id == household_id)
        )
        prefs = prefs_result.scalar_one_or_none()
        if prefs is None:
            prefs = HouseholdPreferences(
                household_id         = household_id,
                preferred_address_id = addr.id,
            )
            db.add(prefs)
        elif not prefs.preferred_address_id:
            prefs.preferred_address_id = addr.id
        await db.commit()

    return APIResponse.ok({
        "diet_type":           result.diet_type,
        "preferred_order_day": result.preferred_order_day,
        "weekly_budget_min":   result.weekly_budget_min,
        "weekly_budget_max":   result.weekly_budget_max,
        "brand_preferences":   result.brand_preferences,
        "pantry_seeds":        result.pantry_seeds,
        "confidence_notes":    result.confidence_notes,
        "address_line":        address_line,
    })


@router.post("/profile", response_model=APIResponse)
async def save_profile(
    body: HouseholdProfileRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Save household profile from questionnaire."""
    from app.services.onboarding_service import OnboardingService
    household_id = _get_household_id(request)
    await OnboardingService(db).save_profile(household_id, body)
    return APIResponse.ok({"household_id": household_id, "onboarding_step": "whatsapp_verification"})


@router.post("/whatsapp/send-otp", response_model=APIResponse)
async def send_otp(
    body: OTPRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Send OTP to WhatsApp number for verification."""
    from app.services.onboarding_service import OnboardingService
    household_id = _get_household_id(request)
    await OnboardingService(db).send_otp(household_id, body.whatsapp_number)
    return APIResponse.ok({"otp_sent": True, "expires_in_seconds": 600})


@router.post("/whatsapp/verify-otp", response_model=APIResponse)
async def verify_otp(
    body: OTPVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Verify OTP and link WhatsApp number."""
    from app.services.onboarding_service import OnboardingService, OTPInvalidError, OTPExpiredError, OTPMaxAttemptsError
    household_id = _get_household_id(request)
    try:
        await OnboardingService(db).verify_otp(household_id, body.whatsapp_number, body.otp)
        return APIResponse.ok({"whatsapp_verified": True, "onboarding_step": "first_basket_preview"})
    except OTPExpiredError as e:
        return APIResponse.fail("OTP_EXPIRED", str(e))
    except OTPMaxAttemptsError as e:
        return APIResponse.fail("OTP_MAX_ATTEMPTS", str(e))
    except OTPInvalidError as e:
        return APIResponse.fail("OTP_INVALID", str(e))


@router.get("/basket-preview", response_model=APIResponse)
async def basket_preview(request: Request, db: AsyncSession = Depends(get_db)):
    """Generate first basket preview (dry run — no placement)."""
    from app.services.onboarding_service import OnboardingService
    from app.services.auth_service import AuthService
    household_id = _get_household_id(request)
    token = await AuthService(db).get_valid_token(household_id)
    basket = await OnboardingService(db).generate_preview_basket(household_id, token)
    return APIResponse.ok(basket)


@router.post("/complete", response_model=APIResponse)
async def complete_onboarding(
    body: OnboardCompleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Mark onboarding complete and schedule first loop."""
    from app.services.onboarding_service import OnboardingService
    household_id = _get_household_id(request)
    next_run_at = await OnboardingService(db).complete_onboarding(household_id, body.send_basket_now)
    return APIResponse.ok({
        "onboarding_complete": True,
        "next_basket_at": next_run_at.isoformat() if next_run_at else None,
        "basket_sent_to_whatsapp": body.send_basket_now
    })
