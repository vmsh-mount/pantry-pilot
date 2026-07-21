from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel

PantryStatus = Literal["stocked", "low", "depleted"]


class PantryItemOut(BaseModel):
    id: str
    item_name: str
    category: str
    standard_unit: str
    estimated_qty_remaining: float
    reorder_threshold: float
    avg_weekly_consumption: Optional[float]
    last_ordered_qty: Optional[float]
    last_ordered_at: Optional[datetime]
    times_ordered: int
    status: PantryStatus


class PantryItemUpdate(BaseModel):
    estimated_qty_remaining: float
