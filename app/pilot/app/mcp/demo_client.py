"""
DemoSwiggyMCPClient — drop-in replacement for SwiggyMCPClient when
SWIGGY_MCP_MODE=demo, for recording the product demo without depending on
the real Swiggy MCP endpoint being up.

Only _call() is overridden: every public method (get_addresses,
search_products, get_cart, checkout, ...) is inherited unchanged, so all the
real parsing logic (_parse_product, _parse_cart, MCPAddress construction,
the dry-run checkout short-circuit) runs exactly as it does against the real
API — just fed from demo_catalog.py instead of an HTTP call.

Cart state lives in a module-level dict keyed by access token, since a new
client is constructed per request but the cart must persist across the
add-item / checkout request pair. In-memory only — resets on pilot restart.
This is intentional: it's a demo fixture, not a persistence layer.
"""

import time
import uuid

from app.mcp.swiggy import SwiggyMCPClient, SwiggyMCPError
from app.mcp.demo_catalog import CATALOG, DEMO_ADDRESS, search_catalog, go_to_items
from app.utils.logging import get_logger

logger = get_logger(__name__)

_DEMO_CARTS: dict[str, list[dict]] = {}

_CATALOG_BY_ID = {item["productId"]: item for item in CATALOG}


def _variation_price(item: dict) -> float:
    price_block = item["variations"][0]["price"]
    return float(price_block.get("offerPrice") or price_block.get("mrp") or 0)


class DemoSwiggyMCPClient(SwiggyMCPClient):
    async def _call(self, tool_name: str, params: dict, attempt: int = 0) -> dict:
        start_ms = int(time.monotonic() * 1000)
        result = self._dispatch(tool_name, params)
        latency_ms = int(time.monotonic() * 1000) - start_ms
        logger.info("demo_mcp_call", tool=tool_name, latency_ms=latency_ms)
        return result

    def _dispatch(self, tool_name: str, params: dict) -> dict:
        if tool_name == "get_addresses":
            return {"addresses": [DEMO_ADDRESS]}

        if tool_name == "delete_address":
            return {"success": True}

        if tool_name == "create_address":
            addr = dict(DEMO_ADDRESS)
            addr["id"] = f"demo_addr_{uuid.uuid4().hex[:8]}"
            return {"address": addr}

        if tool_name == "search_products":
            matches = search_catalog(params.get("query", ""), limit=params.get("limit", 10))
            return {"products": matches}

        if tool_name == "your_go_to_items":
            return {"products": go_to_items(), "nextOffset": None}

        if tool_name == "get_cart":
            return self._cart_response()

        if tool_name == "update_cart":
            return self._update_cart(params)

        if tool_name == "clear_cart":
            _DEMO_CARTS[self._token] = []
            return self._cart_response()

        if tool_name == "checkout":
            # Real client's checkout() already short-circuits via
            # pantrypilot_dry_run before reaching _call() at all when dry-run
            # is on (which it is for the demo). This branch only matters if
            # dry-run is ever turned off while still in demo catalog mode.
            cart = _DEMO_CARTS.get(self._token, [])
            grand_total = sum(i["total_price"] for i in cart)
            _DEMO_CARTS[self._token] = []
            return {
                "orderId": f"demo_{uuid.uuid4().hex[:12]}",
                "status": "PLACED",
                "totalAmount": grand_total,
                "estimatedDelivery": "Today, 6–8 PM",
            }

        if tool_name == "get_orders":
            return {"orders": []}

        if tool_name == "get_order_details":
            return {
                "orderId": params.get("orderId", ""),
                "status": "placed",
                "items": [],
                "billDetails": {},
            }

        if tool_name == "track_order":
            return {
                "orderId": params.get("orderId", ""),
                "status": "placed",
                "currentStep": "Order confirmed",
                "estimatedDelivery": "Today, 6–8 PM",
            }

        if tool_name == "report_error":
            return {"success": True}

        raise SwiggyMCPError(f"demo_mcp: unhandled tool {tool_name}")

    def _cart_response(self) -> dict:
        items = _DEMO_CARTS.get(self._token, [])
        item_total = sum(i["total_price"] for i in items)
        return {
            "items": items,
            "item_total": item_total,
            "delivery_fee": 0.0 if item_total == 0 else 20.0,
            "taxes": round(item_total * 0.05, 2),
            "grand_total": item_total + (0.0 if item_total == 0 else 20.0) + round(item_total * 0.05, 2),
        }

    def _update_cart(self, params: dict) -> dict:
        cart: list[dict] = []
        for mcp_item in params.get("items", []):
            sku_id = mcp_item.get("skuId") or mcp_item.get("spinId")
            quantity = mcp_item.get("quantity", 1)
            catalog_item = _CATALOG_BY_ID.get(sku_id)
            if not catalog_item:
                continue
            unit_price = _variation_price(catalog_item)
            cart.append({
                "spinId":      sku_id,
                "name":        catalog_item["displayName"],
                "brand":       catalog_item["brand"],
                "quantity":    quantity,
                "unit":        catalog_item["variations"][0]["quantityDescription"],
                "unit_price":  unit_price,
                "total_price": round(unit_price * quantity, 2),
            })
        _DEMO_CARTS[self._token] = cart
        return self._cart_response()
