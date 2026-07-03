# UI-015 — Basket Editing: In-app review and edit before confirm

**Status:** ⏳ Pending  
**Area:** Frontend + Backend  
**Depends on:** UI-009 (basket preview), BE-002 (category field)  
**Blocks:** nothing  

---

## Problem

The current confirm flow is binary — approve or skip. Users can't remove an unwanted item or add something the planner missed without switching to WhatsApp. WhatsApp editing works but is text-only and not discoverable. Most users will skip rather than fight the basket, which defeats the automation entirely.

---

## Design

### Cockpit basket screen (extends the dashboard pending basket card)

- Each item row has a **remove button** (✕, tap to remove, no confirm dialog)
- **"+ Add item"** button at the bottom opens a search input — live Swiggy search (debounced 300ms), results appear inline, tap to add
- **Change summary banner** appears after any edit: "Removed 2 · Added 1" — dismissible
- Confirm / Skip CTAs remain sticky at the bottom, always visible
- If all items are removed: hide Confirm, show "Nothing left — skip this week?" prompt

### WhatsApp (existing commands, unchanged)

- Text commands `add milk` / `remove 3, 5` continue to work, now backed by `BasketEditingService`
- Deeplink in confirm message is **deferred** — depends on Twilio template SID update + live domain (tracked separately)

---

## Architecture

### New service: `BasketEditingService`

Extract all add/remove logic currently inlined in `app/tasks/whatsapp.py` into a proper service at `app/pilot/app/services/basket_editing_service.py`. Both the new API endpoints and the WhatsApp task handler will call this service — no duplicated logic.

```
BasketEditingService
  ├── remove_item(db, loop_run_id, item_id) → LoopRunItem | None
  ├── remove_items_by_index(db, loop_run_id, indices) → list[LoopRunItem]
  ├── search_items(db, household_id, query) → list[SwiggyProduct]
  └── add_item(db, loop_run_id, household_id, product: BasketItemAdd) → LoopRunItem
```

**`remove_item`** — hard-deletes the `LoopRunItem` row. No audit table needed for MVP; `added_by` on surviving rows is sufficient signal.

**`add_item`** — creates a new `LoopRunItem` with `added_by="user_added"`.

**`search_items`** — retrieves the household's Swiggy token from `swiggy_tokens`, constructs an MCP client, calls `search_products(query)`, returns up to 10 results. If the token is expired or missing, raises a typed `TokenExpiredError` which the API layer converts to `401 TOKEN_EXPIRED` (not a 5xx). Token expiry during an active edit session is a known gap — the daily `check_token_expiry` beat task does not cover mid-session expiry. For now, the 401 response is the correct behaviour; the frontend should surface "Session expired — please re-authenticate" and redirect to `/reauth`.

---

## What to Build

### 1. `BasketEditingService` — `app/pilot/app/services/basket_editing_service.py`

New file. Contains all four methods above. Refactor `app/tasks/whatsapp.py` `remove_items` and `add_item` intent handlers to call this service instead of inlining the logic.

### 2. New API endpoints — `app/pilot/app/api/basket.py`

| Method | Path | Purpose |
|--------|------|---------|
| `DELETE` | `/v1/basket/item/{item_id}` | Remove single item by `LoopRunItem.id` (UUID) |
| `GET` | `/v1/basket/search?q=...` | Live Swiggy product search, proxied via household MCP token |
| `POST` | `/v1/basket/item` | Add item — body: `BasketItemAdd` |

All three:
- Check session cookie for `household_id` (same auth guard as all other basket endpoints)
- Require `awaiting_confirmation` state on the active run — return `404 NO_PENDING_BASKET` if none

`GET /v1/basket/search` also returns `401 TOKEN_EXPIRED` if the household's Swiggy token is stale.

### 3. `GET /v1/basket/pending` response fix — `app/pilot/app/api/basket.py`

Currently the item list omits `id`. **Add `"id": str(i.id)` to each item dict.** The frontend needs this UUID to call `DELETE /v1/basket/item/{item_id}`. Also add `"category"` and `"added_by"` while touching the serialiser (both fields exist on the model).

### 4. New request/response schemas — `app/pilot/app/schemas/common.py`

```python
class BasketItemAdd(BaseModel):
    swiggy_product_id: str
    name: str
    price: float
    image_url: str | None = None
    category: str | None = None

class BasketSearchResult(BaseModel):
    swiggy_product_id: str
    name: str
    price: float
    image_url: str | None
    brand: str | None
```

### 5. Frontend — `app/cockpit/src/app/dashboard/page.tsx`

- Add ✕ remove button to each item row in the basket card
  - On tap: optimistic remove from local state + call `DELETE /v1/basket/item/{id}`
  - On error: revert local state, show error toast
- Add `+ Add item` button below item list
  - On tap: expand inline `ItemSearchDropdown` (not a modal)
  - Debounced 300ms → `GET /v1/basket/search?q=...`
  - Results list: name + price + brand + tap to add
  - On select: call `POST /v1/basket/item`, append to local state, close search
- Change summary banner: accumulate removed/added counts, show after first edit
- Empty basket state: hide Confirm CTA, show "Nothing left — skip this week?" prompt
- On `401 TOKEN_EXPIRED` from any edit call: show "Session expired" toast, redirect to `/reauth`

### 6. API client — `app/cockpit/src/lib/api.ts`

```ts
removeBasketItem(itemId: string): Promise<APIResponse>
searchBasketItems(query: string): Promise<BasketSearchResult[]>
addBasketItem(product: BasketItemAdd): Promise<APIResponse>
```

### 7. `ItemSearchDropdown` component — `app/cockpit/src/components/basket/ItemSearchDropdown.tsx`

New file in a dedicated `components/basket/` directory (not added to `ui.tsx` — async state + debounce logic is too large for the primitives file). Props: `onSearch(q: string)`, `results: BasketSearchResult[]`, `onSelect(product: BasketSearchResult)`, `loading: boolean`. Renders inline below the trigger button — no modal.

---

## Files to Touch

| File | Action |
|------|--------|
| `app/pilot/app/services/basket_editing_service.py` | **Create** — `BasketEditingService` |
| `app/pilot/app/tasks/whatsapp.py` | Refactor `remove_items` + `add_item` handlers to call `BasketEditingService` |
| `app/pilot/app/api/basket.py` | Fix `GET /pending` item serialiser (add `id`, `category`, `added_by`); add `DELETE /item/{id}`, `GET /search`, `POST /item` |
| `app/pilot/app/schemas/common.py` | Add `BasketItemAdd`, `BasketSearchResult` |
| `app/cockpit/src/app/dashboard/page.tsx` | Inline edit UI — remove per-row, add search, change banner, empty state, TOKEN_EXPIRED redirect |
| `app/cockpit/src/lib/api.ts` | Add `removeBasketItem`, `searchBasketItems`, `addBasketItem` |
| `app/cockpit/src/components/basket/ItemSearchDropdown.tsx` | **Create** — inline search dropdown component |

---

## Known Gaps (not blocking, not in scope)

- **Mid-session token expiry:** `check_token_expiry` beat task runs daily at 9 AM IST. A token that expires during an active edit session will get a `401 TOKEN_EXPIRED` rather than a proactive warning. Acceptable for MVP.
- **WhatsApp deeplink in confirm message:** requires a new Twilio Content Template SID + a live domain. Deferred until post-deploy. Track separately.

---

## Acceptance Criteria

- [ ] `GET /v1/basket/pending` returns `id`, `category`, `added_by` on each item row
- [ ] Tapping ✕ on an item removes it immediately (optimistic) and persists to DB
- [ ] `+ Add item` search input appears inline (no modal), results load within 300ms debounce
- [ ] Tapping a search result adds it to the basket and closes the search input
- [ ] Change summary banner appears after first edit and shows accurate counts
- [ ] After all items removed: Confirm hidden, skip prompt shown
- [ ] Confirm and Skip still work after any combination of edits
- [ ] Re-fetching `GET /v1/basket/pending` after edits returns the updated item list
- [ ] WhatsApp `add X` and `remove N` text commands still work (now backed by `BasketEditingService`)
- [ ] `401 TOKEN_EXPIRED` from any edit endpoint triggers re-auth redirect in the UI
- [ ] `BasketEditingService` has unit tests covering: remove by id, remove by index, add item, empty-basket state, expired-token on search

---

## Out of Scope

- Quantity stepper (post-MVP)
- Undo/redo of edits
- Editing a basket already confirmed or placed
- WhatsApp confirm message deeplink (blocked on Twilio template + domain)
