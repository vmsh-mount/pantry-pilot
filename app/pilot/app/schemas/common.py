from typing import TypeVar, Generic, Optional
from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class APIResponse(BaseModel, Generic[T]):
    success: bool
    data: Optional[T] = None
    error: Optional[ErrorDetail] = None

    @classmethod
    def ok(cls, data: T) -> "APIResponse[T]":
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, code: str, message: str, retryable: bool = False) -> "APIResponse":
        return cls(success=False, error=ErrorDetail(code=code, message=message, retryable=retryable))


class BasketItemAdd(BaseModel):
    sku_id:     str
    item_name:  str
    unit_price: float
    unit:       str = "units"
    image_url:  Optional[str] = None
    category:   Optional[str] = None
    brand:      Optional[str] = None


class BasketSearchResult(BaseModel):
    sku_id:     str
    item_name:  str
    unit_price: float
    unit:       str = "units"
    in_stock:   bool = True
    image_url:  Optional[str] = None
    brand:      Optional[str] = None
