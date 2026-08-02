# PRD — Gate Non-Food Items Out of Nutrition Resolution

**Status:** Ready for implementation
**Date:** 2026-08-02
**Related:** [`nutrition-consumed-not-purchased.md`](nutrition-consumed-not-purchased.md) (same resolution pipeline, different correctness gap), [`nutrition-gap-to-cart-phase-b3-gap-detection.md`](nutrition-gap-to-cart-phase-b3-gap-detection.md) (downstream consumer affected by the fix)

---

## Problem

Nothing in `resolve_item()` checks whether an order item is food before running it through the resolution waterfall. Detergent, soap, shampoo, toothpaste — anything Instamart sells that isn't food — goes through the exact same OFF → USDA → LLM path as rice or paneer.

OFF and USDA are structurally low-risk (food-only databases — see [Root Cause](#root-cause)), but the LLM path has no such structural protection. The only thing standing between a bar of soap and a confidently-cached fake nutrition row is a null-guard that catches it *only when the model happens to return nothing*. Nothing tells the model the item might not be food in the first place — a more compliant or more confident model would just hallucinate plausible-looking macros for "Dove Soap" and it would be cached and served as fact from that point on.

## Root Cause

`_LLM_PROMPT` (`nutrition_resolution.py:422`) opens with:

```
Estimate nutrients per 100g for this food sold on Swiggy Instamart India.
```

— it presupposes the item is food. The system prompt reinforces this: `"You are a nutritional data assistant."` (`nutrition_resolution.py:470`). Nothing in the prompt tells the model an item might not be food, and nothing after the response comes back checks for that either. The one existing guard:

```python
# nutrition_resolution.py:483-486
core_macros = ("calories_per_100g", "protein_per_100g", "total_carbs_per_100g", "fat_per_100g")
if all(data.get(k) is None for k in core_macros):
    return None
```

only fires if the model returns an empty response. It says nothing about whether the item is food — it's a "did the model answer" guard, not a "should the model have answered" guard.

**Why OFF/USDA are lower-risk, but not zero-risk:** both are food-specific databases, so a search for "Dove Soap" is very unlikely to surface an OFF/USDA product with a real `energy-kcal_100g` field. But `_search_off`'s scoring (`nutrition_resolution.py:298-331`) accepts a match on **brand overlap alone** (`score += 2`, and the accept threshold is `best_score < 2` — i.e. `score == 2` passes) with zero product-name token overlap required. If OFF ever returns an unrelated same-brand product (Dove makes both soap and chocolate), a brand-only match could pass today. Narrower and less likely than the LLM risk, but the same root cause: nothing gates on "is this food" before the search runs.

## Why Category Can't Be the Gate

The obvious instinct — filter on `OrderItem.category` — doesn't work with what's in the schema today:

- `pantry_service.py`'s 5-bucket taxonomy (`staples | fresh_produce | dairy | packaged | grocery`) buckets **personal care/cleaning and non-dairy protein into the same `"grocery"` catch-all** (`demo_catalog.py:63,92` — chicken breast, tofu, soap, and detergent are all `category: "grocery"`). A gate on `category == "grocery"` would block real food (chicken, tofu, soya chunks) exactly as often as it blocks soap.
- The real Swiggy MCP's raw `category` field (`_parse_product`, `swiggy.py:469`) is passed through untouched, but its actual taxonomy/content is unverified — the MCP has been returning 403 on core calls since 2026-07-26, so there's no live response to inspect, and the demo catalog deliberately discarded whatever real category info exists in favor of the pantry bucket scheme (see `demo_catalog.py:36-43`).

Whatever gate we build has to work off what's reliably available at resolution time today: `item_name` and `brand` — the same two inputs `_mechanical_food_concept` already classifies from.

## Blast Radius

This isn't just a coverage/display problem — a hallucinated LLM estimate is a real number that flows through the same aggregation path as genuine nutrition:

- `compute_item_totals` scales it like any other resolved item → `OrderNutrition.total_calories`/`total_protein_g`/etc. gets polluted with fabricated soap "calories."
- The weekly digest and dashboard chip inherit that pollution the same way they inherit the [pack-vs-consumed distortion](nutrition-consumed-not-purchased.md) — silently, no flag.
- **Gap-to-Cart's coverage guard** (`nutrition_gaps.py:91`) currently filters on `confidence not in (None, "unresolved")`. If the gate introduces a new terminal state distinct from `"unresolved"` (see [Design](#design) below), this line must also exclude it — otherwise a household that buys a lot of personal-care/cleaning items would have every one of those items counted as a "resolved item with no nutrient value," artificially dragging the coverage denominator down and suppressing real gap detection.

## Goals

1. Recognize non-food items **before** they reach OFF, USDA, or the LLM — not after, via a guard that only sometimes fires.
2. Work off `item_name`/`brand` — the only reliably-populated fields at resolution time — not the coarse `category` bucket.
3. Cache the "not food" determination so the classifier doesn't re-run every time the same SKU is ordered again.
4. Harden the LLM prompt itself as defense-in-depth, for whatever the keyword vocabulary misses.
5. Every downstream consumer of `confidence` (counting, coverage, display) treats the new state correctly — enumerated explicitly below, not discovered piecemeal during implementation.

## Out of Scope

- **Purging already-cached bad rows.** If a non-food item was already resolved and cached before this ships, its `NutritionCache` row is untouched — same "forward-only, no history rewrite" precedent as the companion PRD. A follow-up cleanup task can query for suspicious cached rows once this is live.
- **A real classifier (ML model, dedicated LLM call, Swiggy category taxonomy integration).** A mechanical keyword gate plus prompt hardening is proportionate to the problem; revisit only if the vocabulary approach proves to miss too much in practice.
- **Changing `OrderItem.category`/`PantryItem.category`** or the pantry bucket scheme — unrelated system, already covered by [Why Category Can't Be the Gate](#why-category-cant-be-the-gate) as the reason this PRD doesn't touch it.

---

## Design

### 1. New helper: `_is_non_food()`

Location: `nutrition_resolution.py`, alongside `_mechanical_food_concept` — same inputs, same mechanical/no-LLM philosophy already established in this file.

```python
# Curated, not exhaustive by design — the LLM prompt hardening below is the
# backstop for anything this vocabulary misses. Word-boundary matched, same
# convention as the pantry page's ITEM_ICON_KEYWORDS, so "oil" doesn't match
# inside an unrelated word and "soap" doesn't match inside "soapstone".
_NON_FOOD_KEYWORDS = {
    "soap", "shampoo", "conditioner", "detergent", "toothpaste", "mouthwash",
    "sanitizer", "dishwash", "handwash", "deodorant", "talcum", "lotion",
    "sunscreen", "razor", "shaving cream", "floor cleaner", "toilet cleaner",
    "phenyl", "naphthalene", "diaper", "sanitary pad", "tampon", "tissue",
    "napkin", "insecticide", "mosquito repellent", "air freshener",
    "dishwashing liquid", "fabric softener", "stain remover", "bleach",
}
_NON_FOOD_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _NON_FOOD_KEYWORDS) + r")\b"
)


def _is_non_food(item_name: str, brand: str | None) -> bool:
    """Mechanical, no-LLM check — same inputs and philosophy as
    _mechanical_food_concept. False negatives are expected and handled by
    the LLM prompt hardening in _estimate_llm; false positives are the
    costlier failure mode, so the vocabulary stays conservative."""
    return bool(_NON_FOOD_PATTERN.search(item_name.lower()))
```

### 2. Gate placement: top of `resolve_item()`, before the cache lookup

```python
async def resolve_item(db, sku_id, item_name, brand, qty_desc) -> dict:
    if _is_non_food(item_name, brand):
        return await _cache_not_food(db, sku_id)   # new — see below

    # 1. Redis hot cache
    ...
```

Checked first — before Redis, before DB — so a known non-food SKU never even pays for a cache lookup, and (more importantly) never reaches OFF/USDA/LLM.

### 3. New terminal state: `confidence="not_food"`

Not `"unresolved"` — semantically different. `"unresolved"` means "we tried and couldn't resolve; worth retrying later, e.g. once OFF's catalog improves." `"not_food"` means "resolution doesn't apply here, don't retry." Both `NutritionCache.confidence` and the frontend's `NutritionConfidence` type are plain strings (`String(10)` column, TS string union) — no migration, but every consumer that currently branches on confidence needs to know about the new value:

```python
async def _cache_not_food(db: AsyncSession, sku_id: str) -> dict:
    """Cache a non-food determination so the keyword check doesn't re-run
    on every reorder of the same SKU."""
    resolved = {
        "source": "not_food", "confidence": "not_food",
        "quantity_g": None, "quantity_unresolvable": True,
        "serving_size_g": None, "calories_per_100g": None,
        "protein_per_100g": None, "total_carbs_per_100g": None,
        "fat_per_100g": None, "fiber_per_100g": None,
        "sodium_mg_per_100g": None, "nutrients": {},
        "matched_name": None, "nutriscore_grade": None,
        "food_concept": None, "notable_nutrients": [],
    }
    row = await _db_upsert(db, sku_id, None, True, resolved)
    data = _row_to_dict(row)
    await _redis_set(sku_id, data)
    return data
```

`quantity_unresolvable=True` reuses the existing guard in `compute_item_totals` (`nutrition_resolution.py:676`) — a non-food item's totals are already `None`/skipped with no further change needed there.

### 4. Downstream consumers that must recognize `"not_food"`

Enumerated explicitly so none of these are discovered mid-implementation:

| Location | Current behavior | Required change |
|---|---|---|
| `nutrition_gaps.py:91`, `_weekly_actual_and_coverage` | `confidence not in (None, "unresolved")` | `confidence not in (None, "unresolved", "not_food")` — a non-food item was never a candidate to carry nutrient data, same reasoning already applied to `"unresolved"`. |
| `tasks/nutrition.py`, per-item counting loop (`resolved_count`/`unresolved_count`/`high_conf_count`/`llm_count`) | `"not_food"` would currently fall into the `else: resolved_count += 1` branch alongside genuine resolutions | Add a `non_food_count` bucket; don't count it as "resolved" (it wasn't) or "unresolved" (it's not pending retry). |
| `OrderNutrition.resolved_items`/`total_items` | Same loop, same miscount risk | Follows from the above — `resolved_items` should not include non-food items. |
| `NutritionConfidence` (TS, `api.ts`) + `ConfidenceIcon`/`ConfidenceBadge` (`NutritionCard.tsx`) | 5-state union (`verified\|high\|medium\|estimate\|unresolved`) has no 6th case | **Recommended:** filter items with `confidence === "not_food"` out of the per-item breakdown display entirely, rather than adding a 6th glyph — "Detergent: no nutrition data" is noise in a nutrition card, not signal. This is a product call, not purely technical; flagging it here rather than deciding unilaterally in code. |
| `_db_upsert`'s `_rank` dict (`nutrition_resolution.py:569`) | `_rank.get(resolved["confidence"], 0)` — unranked values default to rank 0 | No change needed: rank 0 means any future real resolution (OFF/USDA/LLM succeeding, in the unlikely event the keyword gate false-positived) would overwrite a `"not_food"` row, which is the correct direction to err. |

### 5. Defense-in-depth: prompt hardening

Belt-and-suspenders for whatever the keyword vocabulary misses (it's curated, not exhaustive — see Goal 4). Add one line to `_LLM_PROMPT` and one check after parsing:

```
If this item is not food or a beverage (e.g. it's a cleaning product,
personal care item, or household good), return {"not_food": true} and
null for every numeric field.
```

```python
# after json.loads(raw), before the core_macros guard
if data.get("not_food") is True:
    return {"not_food_signal": True}
```

**The translation has to happen at the actual `resolve_item()` call site** (`nutrition_resolution.py:626`), not left implicit — `{"not_food_signal": True}` is a non-empty dict, so it's truthy and would otherwise fall straight through the existing `if not resolved:` unresolved-handling block into `_db_upsert`, which builds its fields via direct bracket access (`resolved["source"]`, `resolved["confidence"]`) — neither key exists on that sentinel, so it would raise `KeyError: 'source'` the first time the LLM path actually returns it. The check for the sentinel must come **before** the existing falsy-check, not after:

```python
# nutrition_resolution.py:626, replacing the current line
resolved = await _estimate_llm(item_name, brand, qty_desc)
if resolved and resolved.get("not_food_signal"):
    return await _cache_not_food(db, sku_id)
if not resolved:
    ...   # existing unresolved-handling block, unchanged
```

This only helps for items that reach the LLM (i.e., the keyword gate already missed them) — it's a second layer, not a replacement for gating before the network call.

**Note for implementation, not a design change:** if a SKU was previously cached with real food data (e.g. `confidence="high"`) and a later reorder trips the gate (e.g. a display-name change starts matching a keyword), `_cache_not_food` → `_db_upsert`'s rank check (`0 >= 3`) is `False`, so the existing real row is correctly left untouched and `_db_upsert` returns *that* row — `_row_to_dict` on it genuinely reports `confidence="high"`, not `"not_food"`. Self-correcting, not a bug — every downstream consumer reads the returned `confidence`, not which branch fired — but worth a one-line code comment at that rank check when implemented, so it doesn't read as a silent no-op to a future reader.

---

## Worked Examples

**"Dove Soap" (demo catalog `dp_soap_050`, `category: "grocery"`):**
`_is_non_food("Dove Soap", "Dove")` → matches `"soap"` → short-circuits to `_cache_not_food` → never reaches OFF/USDA/LLM. Zero calories attributed, `confidence="not_food"`, excluded from Gap-to-Cart coverage.

**"Nutrela Soya Chunks" (demo catalog `dp_soya_016`, also `category: "grocery"` — same bucket as soap):**
`_is_non_food("Nutrela Soya Chunks", "Nutrela")` → no keyword match → proceeds through OFF/USDA/LLM normally. Proves the fix discriminates on the item itself, not the coarse category bucket that would have wrongly excluded this real food alongside the soap.

**"Surf Excel Detergent" reaching the LLM anyway (hypothetical vocabulary gap, e.g. a misspelled or unlisted product name):**
Prompt hardening's `not_food: true` response still catches it, one layer deeper than the keyword gate.

---

## Testing Plan

**Unit tests** (new, `tests/unit/test_nutrition_non_food_gate.py`):
- `_is_non_food`: positive matches across the curated vocabulary; negative on real food including ones sharing the `"grocery"` bucket (soya chunks, chicken breast, tofu); word-boundary check (a food item whose name happens to contain a substring of a keyword, e.g. nothing in the current vocabulary collides, but assert the regex is boundary-anchored, not substring).
- `resolve_item`: non-food item short-circuits before any OFF/USDA/LLM call (mock all three, assert none invoked) and returns `confidence="not_food"`.
- `_estimate_llm`: a mocked LLM response with `{"not_food": true}` is translated correctly, without requiring the keyword gate to have caught it first.

**Integration test** (new or extend `tests/integration/test_nutrition_gaps.py`):
- A week's orders mixing food and non-food items → Gap-to-Cart coverage is computed only over genuinely food-eligible items; non-food items don't appear in the coverage denominator.

**Regression check:** re-run `test_nutrition_resolution_gap_to_cart.py` and `test_nutrition_gaps.py` after implementation — neither currently exercises this path, but both build `item_breakdown`-shaped fixtures that could incidentally assume every item has a "real" confidence value.

## Rollout Notes

- Forward-only, same as the companion PRD: existing cached `NutritionCache` rows for already-resolved non-food SKUs (if any) are not retroactively corrected. A follow-up task could scan for suspicious rows (e.g. `source="llm"` with a `food_concept` that doesn't map to any real food) once this has been live for a while.
- The keyword vocabulary in `_NON_FOOD_KEYWORDS` is a living list — extend it as new false negatives are found in practice, rather than treating it as a one-time exhaustive catalog.
