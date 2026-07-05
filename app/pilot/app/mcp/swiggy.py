"""
SwiggyMCPClient — wraps all 13 Swiggy Instamart MCP tools.

Every tool call goes through _call(), which handles:
  - Bearer token injection
  - MCP protocol formatting (tool_name + params → POST body)
  - HTTP error normalisation → PantryPilot exceptions
  - Retry on transient 5xx (max 2 retries, exponential backoff)
  - Structured logging (tool name, latency, status — NO token in logs)

Endpoint: POST https://mcp.swiggy.com/im
Protocol: Model Context Protocol over streamable HTTP
"""

import time
import asyncio
from typing import Any

import httpx

from app.config import get_settings
from app.mcp.types import (
    MCPAddress, MCPProduct, MCPSearchResult,
    MCPCart, MCPCartItem,
    MCPOrder, MCPOrderSummary, MCPCheckoutResult, MCPOrderStatus,
    MCPGoToItem,
)
from app.utils.exceptions import (
    TokenExpiredError, SessionRevokedError, SwiggyMCPError,
    CheckoutFailedError,
)
from app.utils.logging import get_logger

logger        = get_logger(__name__)
_TIMEOUT      = httpx.Timeout(15.0, connect=5.0)
_MAX_RETRIES  = 2
_RETRY_CODES  = {500, 502, 503, 504}


class SwiggyMCPClient:
    """
    Async client for Swiggy Instamart MCP.
    One instance per request — initialised with the household's access token.
    """

    def __init__(self, access_token: str):
        self._token    = access_token
        self._endpoint = f"{get_settings().swiggy_mcp_base_url}/im"
        self._headers  = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json, text/event-stream",
        }

    # ── Discover Tools ────────────────────────────────────────────────────────

    async def get_addresses(self) -> list[MCPAddress]:
        """Fetch all saved delivery addresses for the authenticated user."""
        result = await self._call("get_addresses", {})
        addresses = result.get("addresses", [])
        return [
            MCPAddress(
                id=a["id"],
                address_line=a.get("addressLine", ""),
                phone_number=a.get("phoneNumber"),
                address_category=a.get("addressCategory"),
                address_tag=a.get("addressTag"),
                # Mark as default if tag contains "home" (e.g. "New HOME", "Home")
                is_default="home" in (a.get("addressTag") or "").lower(),
            )
            for a in addresses
        ]

    async def create_address(self, address_data: dict) -> MCPAddress:
        """Create a new delivery address for the authenticated user."""
        result = await self._call("create_address", {"address": address_data})
        a = result.get("address", result)
        return MCPAddress(
            id=a["id"],
            address_line=a.get("addressLine", ""),
            phone_number=a.get("phoneNumber"),
            address_category=a.get("addressCategory"),
            address_tag=a.get("addressTag"),
        )

    async def delete_address(self, address_id: str) -> bool:
        """Delete a saved delivery address."""
        # Swiggy param: addressId (camelCase)
        result = await self._call("delete_address", {"addressId": address_id})
        return result.get("success", False)

    async def search_products(
        self,
        query:      str,
        address_id: str,
        limit:      int = 10,
    ) -> MCPSearchResult:
        """
        Search the Instamart catalogue.
        Returns ranked list of matching SKUs with price and stock status.
        """
        # Swiggy params are camelCase: addressId (not address_id)
        result = await self._call("search_products", {
            "query":     query,
            "addressId": address_id,
            "limit":     limit,
        })
        raw_products = result.get("products", result.get("items", []))
        logger.info("search_products_raw", query=query, keys=list(result.keys()), raw_count=len(raw_products), sample=str(result)[:500])
        products = [_parse_product(p) for p in raw_products]
        return MCPSearchResult(
            products    = products,
            total_count = result.get("totalCount", result.get("total_count", len(products))),
            query       = query,
        )

    async def your_go_to_items(self, address_id: str) -> list[MCPGoToItem]:
        """Fetch frequently / recently ordered items for a delivery address."""
        # Swiggy param: addressId (camelCase)
        result = await self._call("your_go_to_items", {"addressId": address_id})
        items = result.get("items", result.get("products", []))
        return [_parse_go_to_item(i) for i in items]

    # ── Cart Tools ────────────────────────────────────────────────────────────

    async def get_cart(self) -> MCPCart:
        """Get current Instamart cart with all items and bill breakdown."""
        result = await self._call("get_cart", {})
        return _parse_cart(result)

    async def update_cart(self, items: list[dict], address_id: str | None = None) -> MCPCart:
        """
        Replace cart contents with the provided items.
        items: [{"sku_id": "...", "quantity": 1}, ...]
        address_id: Swiggy's address ID (required for cart to be valid at checkout)
        """
        mcp_items = [
            {"spinId": item["sku_id"], "quantity": max(1, int(item["quantity"]))}
            for item in items
            if int(item.get("quantity", 0)) > 0
        ]
        params: dict = {"items": mcp_items}
        if address_id:
            params["selectedAddressId"] = address_id
        result = await self._call("update_cart", params)
        return _parse_cart(result)

    async def clear_cart(self) -> bool:
        """Remove all items from the Instamart cart."""
        result = await self._call("clear_cart", {})
        return result.get("success", True)

    # ── Order Tools ───────────────────────────────────────────────────────────

    async def checkout(
        self,
        address_id:      str,
        delivery_slot:   str   = "evening",
        estimated_total: float = 0.0,
    ) -> MCPCheckoutResult:
        """
        Place and confirm an Instamart order.
        Raises CheckoutFailedError if Swiggy rejects the order.
        """
        if get_settings().pantrypilot_dry_run:
            import uuid as _uuid
            fake_order_id = f"dry_run_{_uuid.uuid4().hex[:12]}"
            logger.info("dry_run_checkout_skipped", fake_order_id=fake_order_id)
            return MCPCheckoutResult(
                order_id           = fake_order_id,
                status             = "PLACED",
                grand_total        = estimated_total,
                estimated_delivery = "Dry run — no order placed",
            )

        # Swiggy params: addressId, deliverySlot (camelCase)
        result = await self._call("checkout", {
            "addressId":    address_id,
            "deliverySlot": delivery_slot,
        })

        data = result.get("data", result)
        order_id = data.get("orderId") or data.get("order_id") or result.get("orderId") or result.get("order_id")
        if not order_id:
            raise CheckoutFailedError(
                f"Checkout succeeded but no orderId returned: {result}"
            )

        return MCPCheckoutResult(
            order_id           = order_id,
            status             = data.get("status") or result.get("status", "placed"),
            grand_total        = float(
                data.get("cartTotal") or data.get("grandTotal") or data.get("grand_total") or
                result.get("grandTotal") or result.get("grand_total") or 0.0
            ),
            estimated_delivery = (
                data.get("estimatedDelivery") or data.get("estimated_delivery") or
                result.get("estimatedDelivery") or result.get("estimated_delivery")
            ),
        )

    # ── Track Tools ───────────────────────────────────────────────────────────

    async def get_orders(self, limit: int = 20) -> list[MCPOrderSummary]:
        """Fetch order history. Used at onboarding and for pantry state sync."""
        result = await self._call("get_orders", {"limit": limit})
        logger.info("get_orders_raw", top_level_keys=list(result.keys()) if isinstance(result, dict) else type(result).__name__, sample=str(result)[:300])
        return [
            MCPOrderSummary(
                order_id=o["orderId"],
                status=o.get("status", "unknown"),
                placed_at=o.get("createdAt"),
                grand_total=float(o.get("totalAmount", 0)),
                item_count=o.get("itemCount", len(o.get("items", []))),
                items=o.get("items", []),
            )
            for o in result.get("orders", [])
        ]

    async def get_order_details(self, order_id: str) -> MCPOrder:
        """Fetch full details of a specific order including all line items."""
        # Swiggy param: orderId (camelCase)
        result = await self._call("get_order_details", {"orderId": order_id})
        raw_items = result.get("items", [])
        items = [
            MCPOrderItem(
                sku_id      = i.get("itemId", i.get("sku_id", "")),
                name        = i.get("name", ""),
                brand       = i.get("brand"),
                category    = i.get("category"),
                quantity    = float(i.get("quantity", 1)),
                unit        = i.get("unit", "pcs"),
                unit_price  = float(i.get("unitPrice") or i.get("unit_price") or 0),
                total_price = float(i.get("totalPrice") or i.get("total_price") or 0),
            )
            for i in raw_items
        ]
        bill = result.get("billDetails", {})
        return MCPOrder(
            order_id      = result.get("orderId", order_id),
            status        = result.get("status", "placed"),
            placed_at     = result.get("createdAt") or result.get("placed_at"),
            delivered_at  = result.get("updatedAt") or result.get("delivered_at"),
            item_total    = float(bill.get("itemTotal") or result.get("item_total") or 0),
            delivery_fee  = float(bill.get("deliveryFee") or result.get("delivery_fee") or 0),
            taxes         = float(bill.get("taxes") or result.get("taxes") or 0),
            grand_total   = float(bill.get("grandTotal") or result.get("totalAmount") or 0),
            address_id    = result.get("deliveryAddress", {}).get("id"),
            delivery_slot = result.get("deliverySlot") or result.get("delivery_slot"),
            items         = items,
        )

    async def track_order(self, order_id: str) -> MCPOrderStatus:
        """Get real-time order tracking status."""
        # Swiggy param: orderId (camelCase)
        result = await self._call("track_order", {"orderId": order_id})
        return MCPOrderStatus(
            order_id           = order_id,
            status             = result.get("status", "unknown"),
            current_step       = result.get("currentStep") or result.get("current_step"),
            estimated_delivery = result.get("estimatedDelivery") or result.get("estimated_delivery"),
            delivery_partner   = result.get("deliveryPartner") or result.get("delivery_partner"),
        )

    # ── Support Tools ─────────────────────────────────────────────────────────

    async def report_error(self, error_type: str, details: dict) -> bool:
        """Report an error to the Swiggy MCP team."""
        result = await self._call("report_error", {
            "error_type": error_type,
            "details":    details,
        })
        return result.get("reported", False)

    # ── Internal: MCP call engine ─────────────────────────────────────────────

    async def _call(
        self,
        tool_name:  str,
        params:     dict[str, Any],
        attempt:    int = 0,
    ) -> dict:
        """
        Core MCP tool invocation.
        Formats the MCP request, handles auth, retries, and error normalisation.
        """
        body = {
            "jsonrpc": "2.0",
            "id":      1,
            "method":  "tools/call",
            "params":  {
                "name":      tool_name,
                "arguments": params,
            },
        }

        start_ms = int(time.monotonic() * 1000)

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    self._endpoint,
                    json    = body,
                    headers = self._headers,
                )

            latency_ms = int(time.monotonic() * 1000) - start_ms

            logger.info(
                "mcp_call",
                tool       = tool_name,
                status     = response.status_code,
                latency_ms = latency_ms,
            )

            return self._handle_response(tool_name, response, attempt)

        except httpx.TimeoutException:
            logger.warning("mcp_timeout", tool=tool_name, attempt=attempt)
            return await self._retry_or_raise(
                tool_name, params, attempt,
                SwiggyMCPError(f"{tool_name} timed out after {_TIMEOUT.read}s")
            )

        except httpx.RequestError as e:
            logger.error("mcp_request_error", tool=tool_name, error=str(e))
            return await self._retry_or_raise(
                tool_name, params, attempt,
                SwiggyMCPError(f"Network error calling {tool_name}: {e}")
            )

    def _handle_response(
        self,
        tool_name: str,
        response:  httpx.Response,
        attempt:   int,
    ) -> dict:
        """Normalise HTTP response → result dict or raise appropriate exception."""

        # ── Auth errors ───────────────────────────────────────────────────────
        if response.status_code == 401:
            raise TokenExpiredError(
                "Swiggy access token expired or invalid."
            )

        if response.status_code == 419:
            raise SessionRevokedError(
                "Swiggy session was revoked. Full re-authentication required."
            )

        # ── Transient server errors (retryable) ───────────────────────────────
        if response.status_code in _RETRY_CODES:
            raise SwiggyMCPError(
                f"{tool_name} returned {response.status_code}. Will retry."
            )

        # ── Client errors (non-retryable) ─────────────────────────────────────
        if response.status_code == 400:
            body = _safe_json(response)
            raise SwiggyMCPError(
                f"{tool_name} bad request: {body.get('error', response.text)}"
            )

        if not response.is_success:
            raise SwiggyMCPError(
                f"{tool_name} unexpected status {response.status_code}"
            )

        # ── Parse MCP JSON-RPC response ───────────────────────────────────────
        body = _safe_json(response)

        if "error" in body:
            rpc_error = body["error"]
            raise SwiggyMCPError(
                f"{tool_name} RPC error {rpc_error.get('code')}: "
                f"{rpc_error.get('message', 'unknown')}"
            )

        # Swiggy MCP responses have two parts:
        #   result.structuredContent — machine-readable dict  ← prefer this
        #   result.content[0].text   — human-readable string  ← fallback only
        import json as _json
        result = body.get("result", body)
        if isinstance(result, dict):
            # Prefer structuredContent when present and non-empty
            sc = result.get("structuredContent")
            if sc and isinstance(sc, dict):
                return sc

            # Fall back: try to JSON-parse the text content
            content = result.get("content", [])
            if isinstance(content, list) and content:
                first = content[0]
                if first.get("type") == "text":
                    try:
                        return _json.loads(first["text"])
                    except (ValueError, KeyError):
                        return {"raw": first.get("text", "")}

        return result if isinstance(result, dict) else {"result": result}

    async def _retry_or_raise(
        self,
        tool_name: str,
        params:    dict,
        attempt:   int,
        exc:       SwiggyMCPError,
    ) -> dict:
        """Retry with exponential backoff or raise if max retries exceeded."""
        if attempt < _MAX_RETRIES:
            backoff = (2 ** attempt) * 0.5  # 0.5s, 1s
            logger.info(
                "mcp_retry",
                tool    = tool_name,
                attempt = attempt + 1,
                backoff = backoff,
            )
            await asyncio.sleep(backoff)
            return await self._call(tool_name, params, attempt + 1)

        logger.error(
            "mcp_max_retries_exceeded",
            tool    = tool_name,
            attempt = attempt,
        )
        raise exc


# ── Private helpers ───────────────────────────────────────────────────────────

def _parse_product(p: dict) -> "MCPProduct":
    """Map a raw Swiggy search_products item to MCPProduct.

    Swiggy's search result groups variants under each product.
    We use the first variation as the canonical SKU.

    Real shape:
      {
        "displayName": "Onion (Eerulli)",
        "brand": "Fruits & Vegetables Category",
        "inStock": true,
        "variations": [{
          "spinId": "FG8WDTXSUN",        ← the SKU ID used for cart
          "quantityDescription": "1 kg",
          "displayName": "...",
          "brandName": "...",
          "price": {"mrp": 39, "offerPrice": 30},
          "isInStockAndAvailable": true,
          "imageUrl": "...",
        }],
        "productId": "4M2JLMBEXY",
      }
    """
    variations  = p.get("variations") or []
    first_var   = variations[0] if variations else {}
    price_block = first_var.get("price") or {}

    return MCPProduct(
        sku_id      = first_var.get("spinId") or p.get("productId") or "",
        name        = first_var.get("displayName") or p.get("displayName") or "",
        brand       = first_var.get("brandName") or p.get("brand"),
        category    = p.get("category"),
        quantity    = first_var.get("quantityDescription"),
        unit        = None,   # embedded in quantityDescription ("1 kg")
        price       = float(price_block.get("offerPrice") or price_block.get("mrp") or 0),
        mrp         = float(price_block.get("mrp") or 0),
        in_stock    = first_var.get("isInStockAndAvailable", p.get("inStock", True)),
        image_url   = first_var.get("imageUrl"),
        description = None,
    )


def _parse_go_to_item(i: dict) -> "MCPGoToItem":
    """Map a raw Swiggy go-to item dict to MCPGoToItem.

    Real shape (same structure as search_products):
      {
        "displayName": "Tata Salt",
        "brand": "Tata",
        "inStock": true,
        "variations": [{
          "spinId": "SPIN123",
          "displayName": "Tata Salt 1kg",
          "brandName": "Tata",
          "price": {"mrp": 28, "offerPrice": 26},
          "isInStockAndAvailable": true,
        }],
        "productId": "PROD123",
      }
    """
    variations  = i.get("variations") or []
    first_var   = variations[0] if variations else {}
    price_block = first_var.get("price") or {}

    price = float(
        price_block.get("offerPrice")
        or price_block.get("mrp")
        or i.get("price")
        or i.get("offerPrice")
        or 0
    )
    sku_id = (
        first_var.get("spinId")
        or i.get("productId")
        or i.get("id")
        or i.get("sku_id")
        or i.get("itemId", "")
    )
    name = (
        first_var.get("displayName")
        or i.get("displayName")
        or i.get("name", "")
    )
    brand = (
        first_var.get("brandName")
        or i.get("brand")
    )
    in_stock = first_var.get("isInStockAndAvailable", i.get("inStock", True))

    return MCPGoToItem(
        sku_id          = sku_id,
        name            = name,
        brand           = brand,
        price           = price,
        in_stock        = in_stock,
        last_ordered_at = i.get("lastOrderedAt") or i.get("last_ordered_at"),
        order_count     = i.get("orderCount") or i.get("order_count") or 0,
    )


def _safe_json(response: httpx.Response) -> dict:
    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


def _parse_cart(result: dict) -> MCPCart:
    if "raw" in result and "items" not in result:
        raise SwiggyMCPError(f"update_cart failed: {result['raw']}")
    items = [
        MCPCartItem(
            sku_id      = i.get("spinId") or i.get("sku_id", ""),
            name        = i.get("name", ""),
            brand       = i.get("brand"),
            quantity    = i.get("quantity", 1),
            unit        = i.get("unit", "pcs"),
            unit_price  = i.get("unit_price", 0.0),
            total_price = i.get("total_price", 0.0),
        )
        for i in result.get("items", [])
    ]
    return MCPCart(
        items        = items,
        item_total   = result.get("item_total", 0.0),
        delivery_fee = result.get("delivery_fee", 0.0),
        taxes        = result.get("taxes", 0.0),
        grand_total  = result.get("grand_total", 0.0),
        item_count   = len(items),
    )
