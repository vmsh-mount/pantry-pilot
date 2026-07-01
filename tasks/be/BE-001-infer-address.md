# BE-001 — `/onboard/infer` Return Address Line

**Status:** ⏳ Pending  
**Area:** Backend  
**Depends on:** nothing  
**Blocks:** UI-007

---

## Problem

`GET /v1/onboard/infer` returns `preferred_address_id` (a UUID referencing the local `addresses` table). The onboarding inference summary screen needs to display a human-readable address string like "Koramangala, Bengaluru" — not a UUID.

---

## Fix

In `app/pilot/app/api/onboard.py`, the `run_inference` endpoint (or wherever inference is returned) should join the `addresses` table on `preferred_address_id` and include `address_line` in the response.

```python
# After fetching inference result, look up address
from app.models.db import Address
addr_result = await db.execute(
    select(Address).where(Address.id == prefs.preferred_address_id)
)
addr = addr_result.scalar_one_or_none()
address_line = addr.address_line if addr else None
```

Add `address_line: str | None` to the inference response payload.

---

## Files to Touch

| File | Action |
|------|--------|
| `app/pilot/app/api/onboard.py` | Add address join + `address_line` in response |
| `app/pilot/app/models/db.py` | Verify `Address` model has `address_line` column |

---

## Acceptance Criteria

- [ ] `GET /v1/onboard/infer` response includes `address_line` as a string
- [ ] Value is human-readable (e.g. "Koramangala, Bengaluru"), not a UUID
- [ ] Returns `null` gracefully if no address is set
