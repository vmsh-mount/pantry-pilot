"""
Tests for SwiggyMCPClient.

Covers:
  - 401 → TokenExpiredError
  - 419 → SessionRevokedError
  - 5xx → SwiggyMCPError (retried up to _MAX_RETRIES times)
  - Timeout → retry then raise
  - Happy path: search_products, get_cart, checkout
  - JSON-RPC error in response body → SwiggyMCPError
  - MCP content envelope parsing
"""

import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from app.mcp.swiggy import SwiggyMCPClient, _parse_cart
from app.mcp.types import MCPCart, MCPProduct
from app.utils.exceptions import (
    TokenExpiredError, SessionRevokedError, SwiggyMCPError, CheckoutFailedError,
)


def make_client() -> SwiggyMCPClient:
    return SwiggyMCPClient(access_token="test_token_abc")


def mock_response(status: int, body: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.is_success  = 200 <= status < 300
    resp.json.return_value = body
    resp.text = str(body)
    return resp


# ── Auth error handling ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_401_raises_token_expired():
    client = make_client()
    response = mock_response(401, {"error": "unauthorized"})

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
        with pytest.raises(TokenExpiredError):
            await client._call("get_cart", {})


@pytest.mark.asyncio
async def test_419_raises_session_revoked():
    client = make_client()
    response = mock_response(419, {"error": "session_revoked"})

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
        with pytest.raises(SessionRevokedError):
            await client._call("get_cart", {})


# ── Retry logic ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_5xx_retries_and_raises():
    """5xx responses should be retried _MAX_RETRIES times then raise SwiggyMCPError."""
    client   = make_client()
    response = mock_response(503, {"error": "service_unavailable"})

    with patch("httpx.AsyncClient") as mock_http:
        with patch("asyncio.sleep", new_callable=AsyncMock):  # skip actual sleep
            mock_http.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
            with pytest.raises(SwiggyMCPError):
                await client._call("search_products", {"query": "dal", "address_id": "addr_1"})


@pytest.mark.asyncio
async def test_timeout_retries_and_raises():
    """Timeouts should be retried then raise SwiggyMCPError."""
    client = make_client()

    with patch("httpx.AsyncClient") as mock_http:
        with patch("asyncio.sleep", new_callable=AsyncMock):
            mock_http.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.TimeoutException("timed out")
            )
            with pytest.raises(SwiggyMCPError):
                await client._call("get_cart", {})


# ── JSON-RPC error in response ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_jsonrpc_error_in_body_raises():
    client = make_client()
    response = mock_response(200, {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32600, "message": "Invalid request"}
    })

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
        with pytest.raises(SwiggyMCPError) as exc_info:
            await client._call("get_cart", {})
    assert "-32600" in str(exc_info.value)


# ── Happy paths ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_cart_returns_cart():
    client = make_client()
    response = mock_response(200, {
        "result": {
            "items": [
                {
                    "sku_id":      "sku_001",
                    "name":        "Aashirvaad Atta 5kg",
                    "quantity":    1,
                    "unit":        "bag",
                    "unit_price":  280.0,
                    "total_price": 280.0,
                }
            ],
            "item_total":   280.0,
            "delivery_fee": 0.0,
            "taxes":        0.0,
            "grand_total":  280.0,
        }
    })

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
        cart = await client.get_cart()

    assert isinstance(cart, MCPCart)
    assert cart.item_count == 1
    assert cart.grand_total == 280.0


@pytest.mark.asyncio
async def test_checkout_raises_when_no_order_id():
    """checkout must raise CheckoutFailedError if Swiggy returns no order_id."""
    client = make_client()
    response = mock_response(200, {
        "result": {"status": "placed"}   # missing order_id
    })

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
        with pytest.raises(CheckoutFailedError):
            await client.checkout(address_id="addr_1")


@pytest.mark.asyncio
async def test_checkout_success():
    client = make_client()
    response = mock_response(200, {
        "result": {
            "order_id":           "swiggy_order_xyz",
            "status":             "placed",
            "grand_total":        1940.0,
            "estimated_delivery": "Today, 6–8 PM",
        }
    })

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
        result = await client.checkout(address_id="addr_1", delivery_slot="evening")

    assert result.order_id == "swiggy_order_xyz"
    assert result.grand_total == 1940.0
    assert result.estimated_delivery == "Today, 6–8 PM"


# ── MCP content envelope ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_content_envelope_parsed():
    """Some MCP tools return result.content[0].text as a JSON string."""
    import json
    client = make_client()

    inner_data = {"products": [], "total_count": 0}
    response = mock_response(200, {
        "result": {
            "content": [
                {"type": "text", "text": json.dumps(inner_data)}
            ]
        }
    })

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
        result = await client._call("search_products", {"query": "test", "address_id": "a"})

    assert result["total_count"] == 0


# ── _parse_cart helper ────────────────────────────────────────────────────────

def test_parse_cart_empty():
    cart = _parse_cart({})
    assert cart.item_count == 0
    assert cart.grand_total == 0.0


def test_parse_cart_with_items():
    cart = _parse_cart({
        "items": [
            {"sku_id": "s1", "name": "Dal", "quantity": 1, "unit": "kg",
             "unit_price": 145.0, "total_price": 145.0}
        ],
        "item_total":   145.0,
        "grand_total":  145.0,
        "delivery_fee": 0.0,
        "taxes":        0.0,
    })
    assert cart.item_count == 1
    assert cart.items[0].sku_id == "s1"
    assert cart.grand_total == 145.0
