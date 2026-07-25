# PRD — Staging MCP Integration

**Status:** Blocked — awaiting Swiggy staging whitelist  
**Date:** 2026-07-23  
**Branch:** `feature/staging-mcp`

---

## Problem

`PANTRYPILOT_DRY_RUN=true` currently only mocks the `checkout` call. Every other MCP operation — `search_products`, `get_orders`, `update_cart`, `clear_cart`, `your_go_to_items` — still hits production Swiggy. This means dry run can:
- Modify the real cart
- Affect Swiggy's order history used by the planning pipeline
- Send real product searches that may have side effects

Swiggy exposes a staging MCP at `https://mcp.swiggy.com/staging/im` (and `/food`, `/dineout`). Routing all MCP traffic through staging when `dry_run=true` makes the safety boundary the URL itself, not scattered if-checks per method.

---

## Goals

- All MCP calls go to `https://mcp.swiggy.com/staging/im` when `PANTRYPILOT_DRY_RUN=true`
- No code changes required to toggle between environments
- Existing `checkout` dry_run guard retained as a secondary safety net
- Staging base URL is configurable (not hardcoded) in case the path changes

---

## Out of Scope

- Staging auth token management (prod tokens must work against staging — verify before implementing)
- Separate staging OAuth flow or household creation
- Any UI indicator that the app is in dry_run / staging mode
- Staging for food or dineout MCP (IM only, matches current usage)

---

## Blocker — Staging Whitelist Required

Tested on 2026-07-23 with a live prod OAuth token against `https://mcp.swiggy.com/staging/im`. The endpoint returns HTTP 200 but with an application-level rejection:

```json
{
  "success": false,
  "error": { "message": "Access denied: Staging tools are restricted to whitelisted users." },
  "reportId": "ERR-MRX4R7H7-350I"
}
```

Prod endpoint with the same token works correctly (returns real address data).

**Action required:** Contact Swiggy (reference report ID `ERR-MRX4R7H7-350I`) to get the account whitelisted for staging MCP access. Implementation can proceed immediately once access is confirmed.

Note: `swiggy_mcp_staging_base_url` has already been added to `app/config.py` and `.env` in preparation.

---

## Design

### URL selection

`SwiggyMCPClient.__init__` currently builds its endpoint as:

```python
self._endpoint = f"{get_settings().swiggy_mcp_base_url}/im"
```

Change to pick the base URL from the dry_run flag:

```python
settings = get_settings()
base = settings.swiggy_mcp_staging_base_url if settings.pantrypilot_dry_run else settings.swiggy_mcp_base_url
self._endpoint = f"{base}/im"
```

### New setting

Add to `app/config.py`:

```python
swiggy_mcp_staging_base_url: str = "https://mcp.swiggy.com/staging"
```

Can be overridden via `SWIGGY_MCP_STAGING_BASE_URL` in `.env` without code changes.

### Keep the existing `checkout` guard

The mock in `checkout()` that returns a `dry_run_*` order ID stays. Staging checkout should also not place real orders, but the guard ensures correctness even if staging auth is misconfigured or the staging environment is unavailable.

### Logging

Add a log line in `SwiggyMCPClient.__init__` so the active endpoint is visible at startup:

```python
logger.info("mcp_client_init", endpoint=self._endpoint, dry_run=settings.pantrypilot_dry_run)
```

---

## Behaviour difference vs current dry_run

| Scenario | Current | After this change |
|---|---|---|
| `search_products` | hits prod Swiggy | hits staging Swiggy |
| `update_cart` / `clear_cart` | hits prod Swiggy (real cart modified!) | hits staging Swiggy |
| `get_orders` / `your_go_to_items` | hits prod Swiggy | hits staging Swiggy |
| `checkout` | mocked locally, no MCP call | mocked locally + staging URL as fallback |

**Tradeoff:** Staging may return empty or synthetic data (no order history, no saved addresses, dummy products). This makes the planning pipeline produce thinner baskets. If end-to-end pipeline correctness testing is the goal, prod data + mocked checkout is better. If safety against accidental real-side-effects is the goal, staging is better. Both use cases are served by controlling `PANTRYPILOT_DRY_RUN`.

---

## Files to Change

| File | Change |
|---|---|
| `app/pilot/app/config.py` | Add `swiggy_mcp_staging_base_url: str = "https://mcp.swiggy.com/staging"` |
| `app/pilot/app/mcp/swiggy.py` | Pick base URL from `dry_run` flag in `__init__`; add init log line |
| `app/pilot/.env.example` | Document `SWIGGY_MCP_STAGING_BASE_URL` and `PANTRYPILOT_DRY_RUN` together |

No changes to tasks, services, API routes, or frontend.
