# BE-002 — Persist Category Field on Basket Items

**Status:** ⏳ Pending  
**Area:** Backend  
**Depends on:** nothing  
**Blocks:** UI-009, UI-011

---

## Problem

`GET /v1/basket/pending` returns `LoopRunItem` records. The `category` field exists on `LoopRunItem` in the DB model but is not being populated when items are written during the `optimize` node in the planning graph. As a result, the frontend always receives `category: null`, making it impossible to group items by category.

---

## Where Items Are Written

`app/pilot/app/agent/planning_graph.py` — `optimize` node.

Items are inserted as `LoopRunItem(...)` objects. The `category` field must be passed from the candidate basket item (which does have `category` set by the rules engine and LLM additions).

---

## Fix

In the optimize node, when constructing `LoopRunItem`, include `category`:

```python
LoopRunItem(
    loop_run_id         = loop_run_id,
    household_id        = household_id,
    item_name           = item["item_name"],
    category            = item.get("category", "grocery"),   # ← add this
    ...
)
```

Also verify `category` is included in `GET /v1/basket/pending` response serialization in `app/pilot/app/api/basket.py`.

---

## Files to Touch

| File | Action |
|------|--------|
| `app/pilot/app/agent/planning_graph.py` | Add `category` when writing `LoopRunItem` |
| `app/pilot/app/api/basket.py` | Add `category` to item serialization in `get_pending_basket` |

---

## Acceptance Criteria

- [ ] New basket runs persist `category` on each `loop_run_item` row
- [ ] `GET /v1/basket/pending` response includes `category` per item
- [ ] Existing rows without category return `"grocery"` as fallback
