from pydantic import BaseModel


class AuthInitiateResponse(BaseModel):
    redirect_url: str


class AuthCallbackResponse(BaseModel):
    household_id: str
    is_new_user: bool
    redirect_to: str  # /onboard or /settings
