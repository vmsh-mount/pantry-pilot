# UI-014 — Orders History Page

**Status:** ⏳ Pending  
**Area:** Frontend + Backend  
**Depends on:** nothing  
**Blocks:** nothing

---

## Problem

After authenticating with Swiggy, the user has no way to see their order history in the app. The Swiggy MCP already has a working `get_orders()` call — it's just not surfaced anywhere.

---

## Backend

### New file: `app/pilot/app/api/orders.py`

```python
GET /v1/orders
```

- Get valid token for household
- Call `mcp.get_orders(limit=20)`
- Return list of orders with: `order_id`, `placed_at`, `total`, `item_count`, `items` (first 3 for preview), `source` ("pantrypilot" if loop_run exists for this order, else "swiggy")

Register in `app/pilot/app/main.py`.

Add to `app/cockpit/src/lib/api.ts`:
```ts
orders: {
  list: () => request("/orders"),
}
```

---

## Frontend

### New file: `app/cockpit/src/app/orders/page.tsx`

**Layout:**
- Header: "Order History" with back arrow to dashboard
- List of order cards, newest first

**Each order card:**
- Date: "Sun, 29 Jun · 10:02 AM"
- Total + item count: "₹1,952 · 13 items"
- Top 3 items as small tags: "Atta · Toor Dal · Butter"
- If placed via PantryPilot: small green "PantryPilot" badge
- If Swiggy history: no badge (or grey "Swiggy" tag)

**Empty state:**
- If no orders: "No orders yet. Your first basket will appear here once it's placed."

**Error state:**
- If token expired: show re-auth prompt

---

## Dashboard nav update

Add "Orders" tab/link to `app/cockpit/src/app/dashboard/page.tsx` header area.

---

## Files to Touch

| File | Action |
|------|--------|
| `app/pilot/app/api/orders.py` | Create |
| `app/pilot/app/main.py` | Register orders router |
| `app/cockpit/src/lib/api.ts` | Add `orders.list()` |
| `app/cockpit/src/app/orders/page.tsx` | Create |
| `app/cockpit/src/app/dashboard/page.tsx` | Add Orders link in nav |

---

## Acceptance Criteria

- [ ] `GET /v1/orders` returns last 20 orders from Swiggy MCP
- [ ] `/orders` page renders order cards with date, total, item count, preview tags
- [ ] PantryPilot-placed orders show a green badge
- [ ] Empty state shown when no orders exist
- [ ] Auth error redirects to `/`
- [ ] Orders link visible from dashboard
