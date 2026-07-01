from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class HouseholdOut(BaseModel):
    id: str
    household_type: str
    member_count: int
    diet_type: str
    allergies: list[str]
    weekly_budget_min: Optional[int]
    weekly_budget_max: Optional[int]
    onboarding_complete: bool
    is_active: bool
    is_paused: bool
    city: str
    created_at: datetime

    class Config:
        from_attributes = True


class PreferencesOut(BaseModel):
    preferred_order_day: str
    preferred_order_time: str
    preferred_delivery_slot: str
    confirmation_window_hrs: int
    next_run_at: Optional[datetime]
    last_run_at: Optional[datetime]

    class Config:
        from_attributes = True
