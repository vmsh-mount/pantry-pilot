"""
MCP response types for Swiggy Instamart tools.
These are the data shapes returned by each MCP tool call.
"""

from typing import Optional
from pydantic import BaseModel


# ── Address ───────────────────────────────────────────────────────────────────

class MCPAddress(BaseModel):
    # Real Swiggy MCP field names
    id:               str                    # address ID used in search_products / checkout
    address_line:     str = ""               # full formatted address string
    phone_number:     Optional[str] = None
    address_category: Optional[str] = None   # "Other", "Friends & Family", etc.
    address_tag:      Optional[str] = None   # user label e.g. "New HOME", "Work"
    # Derived: True when addressTag contains "home" (case-insensitive)
    is_default:       bool = False


# ── Product / SKU ─────────────────────────────────────────────────────────────

class MCPProduct(BaseModel):
    sku_id:           str   # Swiggy's skuId (canonical product identifier)
    spin_id:          str = ""  # Swiggy's spinId (required alongside skuId in update_cart)
    name:             str
    brand:            Optional[str] = None
    category:         Optional[str] = None
    quantity:         Optional[str] = None    # e.g. "1 kg", "500 ml"
    unit:             Optional[str] = None
    price:            float
    mrp:              Optional[float] = None
    in_stock:         bool = True
    image_url:        Optional[str] = None
    description:      Optional[str] = None


class MCPSearchResult(BaseModel):
    products:         list[MCPProduct]
    total_count:      int = 0
    query:            str


# ── Cart ──────────────────────────────────────────────────────────────────────

class MCPCartItem(BaseModel):
    sku_id:           str
    name:             str
    brand:            Optional[str] = None
    quantity:         float
    unit:             str
    unit_price:       float
    total_price:      float


class MCPCart(BaseModel):
    items:            list[MCPCartItem] = []
    item_total:       float = 0.0
    delivery_fee:     float = 0.0
    taxes:            float = 0.0
    grand_total:      float = 0.0
    item_count:       int = 0


# ── Order ─────────────────────────────────────────────────────────────────────

class MCPOrderItem(BaseModel):
    sku_id:           str
    name:             str
    brand:            Optional[str] = None
    category:         Optional[str] = None
    quantity:         float
    unit:             str
    unit_price:       float
    total_price:      float


class MCPOrder(BaseModel):
    order_id:         str
    status:           str                     # placed | out_for_delivery | delivered | cancelled
    placed_at:        Optional[str] = None    # ISO timestamp
    delivered_at:     Optional[str] = None
    item_total:       float
    delivery_fee:     float = 0.0
    taxes:            float = 0.0
    grand_total:      float
    address_id:       Optional[str] = None
    delivery_slot:    Optional[str] = None
    items:            list[MCPOrderItem] = []


class MCPOrderSummary(BaseModel):
    """Lightweight order record returned by get_orders (list view)."""
    order_id:         str                    # Swiggy: orderId
    status:           str                    # Swiggy: status ("DELIVERED", etc.)
    placed_at:        Optional[str] = None   # Swiggy: createdAt (ISO timestamp)
    grand_total:      float                  # Swiggy: totalAmount
    item_count:       int = 0               # Swiggy: itemCount
    items:            list[dict] = []        # Swiggy: items [{name, quantity, itemId}]


class MCPCheckoutResult(BaseModel):
    order_id:         str
    status:           str
    grand_total:      float
    estimated_delivery: Optional[str] = None  # e.g. "Today, 6–8 PM"


# ── Order Tracking ────────────────────────────────────────────────────────────

class MCPOrderStatus(BaseModel):
    order_id:         str
    status:           str
    current_step:     Optional[str] = None
    estimated_delivery: Optional[str] = None
    delivery_partner: Optional[str] = None


# ── Go-to Items ───────────────────────────────────────────────────────────────

class MCPGoToItem(BaseModel):
    sku_id:           str
    name:             str
    brand:            Optional[str] = None
    price:            float
    in_stock:         bool = True
    last_ordered_at:  Optional[str] = None
    order_count:      int = 0
