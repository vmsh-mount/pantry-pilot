from typing import Optional
from pydantic import BaseModel


class InferredItem(BaseModel):
    name: str
    ordered_times: int


class InferredBrand(BaseModel):
    item: str
    brand: str
    confidence: float


class InferredAddress(BaseModel):
    swiggy_address_id: str
    label: Optional[str]
    area: Optional[str]


class InferenceResult(BaseModel):
    diet_type: Optional[str]
    diet_confidence: float
    weekly_budget_estimate: Optional[int]
    preferred_order_day: Optional[str]
    preferred_order_time: Optional[str]
    top_items: list[InferredItem]
    brand_preferences: list[InferredBrand]
    addresses: list[InferredAddress]
    needs_clarification: list[str]


class HouseholdProfileRequest(BaseModel):
    household_type: str         # solo|couple|family|joint_family
    member_count: int
    diet_type: str              # vegetarian|vegan|jain
    allergies: list[str] = []
    weekly_budget_min: Optional[int] = None
    weekly_budget_max: Optional[int] = None
    preferred_order_day: str = "sunday"
    preferred_order_time: str = "10:00"
    preferred_address_id: Optional[str] = None
    confirmed_inferences: dict = {}


class OTPRequest(BaseModel):
    whatsapp_number: str        # E.164: +919876543210


class OTPVerifyRequest(BaseModel):
    whatsapp_number: str
    otp: str


class OnboardCompleteRequest(BaseModel):
    send_basket_now: bool = False
