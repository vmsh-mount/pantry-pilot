from typing import Optional
from datetime import datetime
from pydantic import BaseModel, field_validator


class RoutineItemIn(BaseModel):
    item_name:           str
    quantity:            float = 1.0
    unit:                str = "unit"
    sku_id:   Optional[str] = None
    swiggy_product_name: Optional[str] = None


class RoutineCreate(BaseModel):
    name:            str
    frequency_type:  str   # every_n_days | weekly | monthly
    frequency_value: int
    schedule_time:   str   # "HH:MM" IST — API converts to UTC before persisting
    duration_preset: Optional[str] = None   # "2_weeks" | "1_month" — if set, end_date computed
    end_date:        Optional[datetime] = None  # explicit end date (if no preset)
    items:           list[RoutineItemIn]

    @field_validator("frequency_type")
    @classmethod
    def validate_frequency_type(cls, v: str) -> str:
        if v not in ("every_n_days", "weekly", "monthly"):
            raise ValueError("frequency_type must be every_n_days, weekly, or monthly")
        return v

    @field_validator("frequency_value")
    @classmethod
    def validate_frequency_value(cls, v: int) -> int:
        if v < 1:
            raise ValueError("frequency_value must be >= 1")
        return v

    @field_validator("items")
    @classmethod
    def validate_items(cls, v: list) -> list:
        if not v:
            raise ValueError("At least one item is required")
        return v


class RoutinePatch(BaseModel):
    name:            Optional[str] = None
    frequency_type:  Optional[str] = None
    frequency_value: Optional[int] = None
    schedule_time:   Optional[str] = None
    end_date:        Optional[datetime] = None
    items:           Optional[list[RoutineItemIn]] = None


class RoutineItemOut(BaseModel):
    id:                   str
    item_name:            str
    quantity:             float
    unit:                 str
    sku_id:    Optional[str]
    swiggy_product_name:  Optional[str]


class RoutineRunOut(BaseModel):
    id:            str
    scheduled_at:  datetime
    status:        str
    skip_reason:   Optional[str]
    placed_at:     Optional[datetime]
    total_amount:  Optional[float]
    order_id:      Optional[str]


class RoutineOut(BaseModel):
    id:               str
    name:             str
    status:           str
    frequency_type:   str
    frequency_value:  int
    schedule_time_ist: str   # "HH:MM" displayed in IST
    start_date:       datetime
    end_date:         Optional[datetime]
    next_run_at:      Optional[datetime]
    runs_remaining:   Optional[int]   # null = ongoing
    total_runs:       Optional[int]   # null = ongoing
    items:            list[RoutineItemOut]
    upcoming_runs:    list[datetime]
