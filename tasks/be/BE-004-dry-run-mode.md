# BE-004 — Dry Run Mode (Order Guard Rail)

**Status:** ⏳ Pending  
**Area:** Backend + Frontend (banner only)  
**Depends on:** nothing

---

## Problem

During local development and end-to-end testing, the full planning pipeline (sense → plan → optimize → confirm → place) triggers a real Swiggy Instamart order. This has caused accidental purchases. There is no guard rail to prevent this.

---

## Design principle

**Mock at the Swiggy MCP client layer only. The flag is read in exactly two places.**

When `PANTRYPILOT_DRY_RUN=true`:
- `SwiggyMCPClient.checkout()` returns a fake response instead of calling the real API
- The `place` node receives this response and proceeds identically to a real order
- Order record created, pantry updated, WhatsApp sent, run history populated
- The `place` node also reads the flag once — purely to prepend `[DRY RUN]` to the WhatsApp receipt message. No logic branch, just a display label.

The flag is intentionally not pushed into the WhatsApp service, Order creation, pantry update, or any other downstream code — they all operate on the checkout response as-is, real or fake.

This preserves the integrity of end-to-end testing. The full pipeline executes. The only thing that doesn't happen is the HTTP call to Swiggy's checkout endpoint.

---

## Toggle

**`PANTRYPILOT_DRY_RUN=true`** in `.env`

- Read into `settings.pantrypilot_dry_run: bool` in `app/config.py`
- Default: `False` (real orders, safe production default)
- To enable: add `PANTRYPILOT_DRY_RUN=true` to `.env`, run `make restart`
- To disable: remove or set to `false`, run `make restart`

---

## Backend changes

### `app/config.py`

Add one field — no alias needed, Pydantic-settings auto-maps `PANTRYPILOT_DRY_RUN` → `pantrypilot_dry_run` (case-insensitive):

```python
pantrypilot_dry_run: bool = False
```

### `app/mcp/swiggy.py` — `SwiggyMCPClient.checkout()`

The only place that checks the flag. Accepts `estimated_total` so the fake response carries a real-looking amount (avoids ₹0 in history):

```python
async def checkout(self, address_id: str, delivery_slot: str = "evening", estimated_total: float = 0.0) -> dict:
    settings = get_settings()
    if settings.pantrypilot_dry_run:
        fake_order_id = f"dry_run_{uuid.uuid4().hex[:12]}"
        logger.info("dry_run_checkout_skipped", fake_order_id=fake_order_id)
        return {
            "orderId":           fake_order_id,
            "status":            "PLACED",
            "totalAmount":       estimated_total,
            "estimatedDelivery": "Dry run — no order placed",
        }
    # ... real Swiggy MCP call unchanged below
```

The fake response mirrors the real Swiggy checkout response shape so the `place` node's parsing code runs unchanged.

The `dry_run_` prefix makes test orders identifiable in DB history and logs.

**Scope:** only `checkout()` is mocked. `clear_cart` and `update_cart` still call real Swiggy in dry run mode — those operations don't place orders or charge money, so they are safe to run.

The `place` node passes the basket estimated total into `checkout()`:
```python
order_result = await mcp_client.checkout(address_id=..., slot=..., estimated_total=basket_total)
```

### `app/api/settings_router.py` — expose dry_run in GET /settings

Add `dry_run: bool` to the settings response so the frontend can read it.

No write endpoint — dry run is infrastructure config, not a user preference. Toggle via `.env` only.

---

## Frontend changes

### `app/cockpit/src/app/dashboard/page.tsx`

Fetch `dry_run` from `GET /v1/settings` on load (alongside basket + runs data).

When `dry_run=True`, show a persistent amber banner at the top of the dashboard:

```
⚠ Test mode — orders won't be placed on Swiggy
```

No dismiss button — it stays visible as long as dry run is active so you can never forget it's on.

### `app/cockpit/src/lib/api.ts`

`settings.get()` already exists. No new call needed — just read `dry_run` from its response.

---

## What the fake order looks like in history

A dry run completed run in `/runs` history looks identical to a real run:
- State: `completed`
- Item count: real items from basket
- Total price: from Order.grand_total (populated from fake checkout `totalAmount`, which mirrors the basket estimated total — not ₹0)
- `swiggy_order_id`: `dry_run_abc123def456` — visually identifiable

**total_price in dry run:** The `place` node passes `estimated_total` (sum of `LoopRunItem.total_price`) into `checkout()`. The fake response returns this value as `totalAmount`, which the `place` node then stores in `Order.grand_total`. Dry run orders show a realistic basket total — not ₹0.

---

## WhatsApp message in dry run

The `place` node reads `settings.pantrypilot_dry_run` once, purely to prefix the WhatsApp receipt with `[DRY RUN] `. This is the only exception to the "one place reads the flag" rule — it's a display label at the call site, not a logic branch. The WhatsApp service itself has no knowledge of dry run mode.

---

## Files to touch

**Backend:**
- `app/pilot/app/config.py` — add `pantrypilot_dry_run` field
- `app/pilot/app/mcp/swiggy.py` — `checkout()` method only
- `app/pilot/app/agent/planning_graph.py` — `place` node: pass `estimated_total` to `checkout()`, prefix WhatsApp receipt with `[DRY RUN]` when flag is set
- `app/pilot/app/api/settings_router.py` — expose `dry_run` in GET response
- `app/pilot/.env` — add `PANTRYPILOT_DRY_RUN=false` (gitignored, local config)
- `app/pilot/.env.example` — document `PANTRYPILOT_DRY_RUN=false` (committed, serves as documentation)

**Frontend:**
- `app/cockpit/src/app/dashboard/page.tsx` — amber banner when `dry_run=True`

**Tests:**
- `app/pilot/tests/integration/test_dry_run.py` — new file

---

## Acceptance criteria

- [ ] `PANTRYPILOT_DRY_RUN=true` in `.env` prevents any HTTP call to Swiggy checkout
- [ ] Fake order ID has `dry_run_` prefix and is unique per run
- [ ] Order record created in DB with fake swiggy_order_id
- [ ] Pantry stock updated after dry run (same as real order)
- [ ] WhatsApp receipt message sent after dry run
- [ ] Run appears in `/runs` history as `completed`
- [ ] `GET /v1/settings` returns `dry_run: true`
- [ ] Dashboard shows amber "Test mode" banner when `dry_run=true`
- [ ] Banner absent when `dry_run=false`
- [ ] Only `swiggy.py:checkout()` and the `place` node's WhatsApp prefix read `settings.pantrypilot_dry_run` — no other file
- [ ] `PANTRYPILOT_DRY_RUN=false` (default) — real checkout call proceeds unchanged
- [ ] Integration test: dry run completes full pipeline, Order record has `dry_run_` prefix in swiggy_order_id

---

## Out of scope

- Per-household dry run (all or nothing — infrastructure config)
- UI toggle for dry run (env var only, intentionally requires restart)
