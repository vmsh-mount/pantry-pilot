from app.mcp.swiggy import SwiggyMCPClient


class SwiggyMCPProvider:
    def __init__(self, access_token: str):
        self._client = SwiggyMCPClient(access_token)

    async def get_addresses(self):
        return await self._client.get_addresses()

    async def search_products(self, query, address_id, limit=10):
        return await self._client.search_products(query, address_id, limit)

    async def your_go_to_items(self, address_id):
        return await self._client.your_go_to_items(address_id)

    async def get_cart(self):
        return await self._client.get_cart()

    async def update_cart(self, items):
        return await self._client.update_cart(items)

    async def clear_cart(self):
        return await self._client.clear_cart()

    async def checkout(self, address_id, delivery_slot=None):
        return await self._client.checkout(address_id, delivery_slot)

    async def get_orders(self, limit=20):
        return await self._client.get_orders(limit)

    async def get_order_details(self, order_id):
        return await self._client.get_order_details(order_id)

    async def track_order(self, order_id):
        return await self._client.track_order(order_id)

    async def report_error(self, error_type, details):
        return await self._client.report_error(error_type, details)
