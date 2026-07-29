from app.providers.mcp.swiggy import SwiggyMCPProvider
from app.mcp.demo_client import DemoSwiggyMCPClient


class DemoSwiggyMCPProvider(SwiggyMCPProvider):
    """Same delegation surface as SwiggyMCPProvider, backed by
    DemoSwiggyMCPClient instead of the real Swiggy MCP HTTP client.
    Selected via SWIGGY_MCP_MODE=demo — see providers/factory.py."""

    def __init__(self, access_token: str):
        self._client = DemoSwiggyMCPClient(access_token)
