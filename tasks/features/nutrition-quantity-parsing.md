# PRD — Fix Quantity Parsing: Stop Silently Dropping and Silently Miscounting Items

**Status:** Ready for implementation
**Date:** 2026-08-02
**Related:** [`nutrition-consumed-not-purchased.md`](nutrition-consumed-not-purchased.md), [`nutrition-non-food-gate.md`](nutrition-non-food-gate.md) (same resolution pipeline; both of those fixes still depend on `quantity_g` being resolved at all — this PRD is upstream of both)

---

## Problem

`_parse_quantity_g()` is the only thing standing between a Swiggy quantity string and a gram figure everything else in the nutrition pipeline depends on. It's a narrow set of regexes, and real Swiggy quantity descriptions break it in two distinct ways:

1. **Silent drop.** A format it doesn't recognize (a bare piece count, "1 dozen") returns `None` → `quantity_unresolvable=True` → the item contributes nothing to nutrition totals, with no flag anywhere that it happened. This isn't hypothetical — see [Root Cause](#root-cause) below for two cases already broken in this codebase's own demo catalog today.
2. **Silent miscount.** A multipack description ("2 x 200 g") isn't recognized as a multipack, but its trailing weight *does* match the plain-weight regex — so it silently returns the single-pack weight (200g) instead of the true total (400g). This is worse than case 1: a confidently wrong number instead of an honest miss.

## Root Cause

`nutrition_resolution.py:101`, `_parse_quantity_g(qty_desc, item_name)`. The count-based branch:

```python
# nutrition_resolution.py:129-133
for unit, weight in _UNIT_WEIGHTS_G.items():
    m = re.search(rf"(\d+)\s*{unit}", s)
    if m:
        return float(m.group(1)) * weight
```

`s` is derived entirely from `qty_desc` (`tasks/nutrition.py:91`: `f"{item.quantity} {item.unit}"` — e.g. `"6.0 pcs"`). This loop searches `s` for a digit immediately followed by a food noun like `"egg"`. But the food noun never appears in `qty_desc` at all — it's in the separate `item_name` string (`"Farm Fresh Eggs"`). The function already receives `item_name` as a parameter and already uses it correctly one branch over, for liquid density:

```python
# nutrition_resolution.py:138-143
def _liquid_density_for(item_name: str) -> float:
    name_lower = item_name.lower()
    for keyword, density in _LIQUID_DENSITY.items():
        if keyword in name_lower:
            return density
    return 1.0
```

The count branch just never adopted the same pattern — it searches the wrong string. This isn't "a few missing formats," it's why the count-based path barely works at all for real data: **"how many" lives in `qty_desc`, "what food it's counting" lives in `item_name`, and they were never cross-referenced.**

**Proof this is live, not theoretical** — `app/mcp/demo_catalog.py`:
- `dp_eggs_015`, `"Farm Fresh Eggs"`, quantity `"6 pcs"` (line 61) — `s = "6.0 pcs"` contains no `"egg"` substring → `quantity_unresolvable=True` today.
- `dp_banana_025`, `"Banana"`, quantity `"1 dozen"` (line 74) — `"dozen"` isn't handled by any regex in the function at all → `quantity_unresolvable=True` today.

**Multipack silent-miscount** — not reproduced in this codebase's fixtures (no multipack quantity string exists in any current mock/demo data; the real Swiggy MCP has been 403ing since July 26 so there's no live response to check either), but `"N x WEIGHT"` is a standard Instamart listing convention for multipacks (yogurt cups, Maggi, soft drinks). Traced mechanically: `re.search(r"([\d.]+)\s*(g|gm|gram|grams)", "2x200g")` — the regex engine fails to match starting at `"2"` (next char `"x"` isn't `"g"`), then succeeds starting at `"200g"`, returning **200, not 400**. Flagged as a real, provable-by-inspection gap even without a live fixture to point to — being direct about that distinction rather than overstating it as already-observed.

## Blast Radius

Two call sites depend on this, both silently affected:

- **`resolve_item()`** (`nutrition_resolution.py:678`) — the main per-order-item nutrition path. A dropped item contributes `None` to every total, same failure shape as the [pack-vs-consumed](nutrition-consumed-not-purchased.md) and [non-food](nutrition-non-food-gate.md) issues: invisible in the weekly digest, invisible in Gap-to-Cart's actual-vs-target diff, with `quantity_unresolvable=True` giving no distinct signal from "we haven't tried yet."
- **`nutrition_gaps.py:269`**, Gap-to-Cart's recommendation "delivers" calculation — `pack_g = resolved.get("quantity_g") or _parse_quantity_g(qty_desc, s["candidate"]["food_concept"])`. When `pack_g` is falsy, the guard at line 270 (`if density is not None and pack_g and p.price`) fails silently and that candidate is dropped from `results` entirely — not shown as a lower-confidence option, just absent. **Eggs are one of the most obvious protein recommendations Gap-to-Cart would ever surface, and they're currently unrecommendable if listed as "6 pcs."**

## Goals

1. Fix the count branch to cross-reference `item_name` for the food noun, the same pattern `_liquid_density_for` already uses — not a new mechanism, a consistency fix.
2. Add `"dozen"` as a count unit and a generic bare piece-count parser (`"pcs"`, `"piece"`, `"unit"`, `"nos"`), decoupled from which food it's counting.
3. Recognize `"N x WEIGHT"` multipacks and compute the true total — and critically, when something looks like a multipack but isn't a shape this handles, **fail safely (return `None`) rather than let a later branch misparse the trailing number.**
4. Stay conservative for unknown foods: if the count is known but there's no per-unit weight for that specific food, stay `unresolvable` — never invent a generic "average piece" weight. Same "false positives costlier than false negatives" principle as the non-food gate.
5. First-ever test coverage for this function (none exists today — see [Testing Plan](#testing-plan)).

## Out of Scope

- **Multipacks of countable items** ("2 x 6 pcs" — 2 packs of 6 eggs). Narrower and rarer than a plain weight/volume multipack; the design below explicitly guards against this being *misparsed* (falls through to `unresolvable`, not a wrong number) but doesn't attempt to *resolve* it. A real gap worth flagging, not silently pretending it's covered.
- **Expanding `_UNIT_WEIGHTS_G`'s food vocabulary beyond what's needed to fix the two proven cases.** Same living-list philosophy as the non-food gate's keyword list — extend incrementally as specific gaps are found in practice, not as a one-time exhaustive catalog.
- **A generic average-piece-weight fallback for foods not in the table.** Deliberately rejected — see Goal 4.
- **Retroactively re-resolving already-cached `quantity_unresolvable=True` rows.** Same forward-only precedent as both companion PRDs.

---

## Design

### 1. Extract count parsing into its own helper

Location: `nutrition_resolution.py`, alongside `_liquid_density_for`.

```python
_DOZEN = 12

def _parse_count(s: str) -> float | None:
    """Extract a bare count from a Swiggy quantity string — "6 pcs",
    "1 dozen", "12 units" — decoupled from which food it's counting
    (that's a separate string, item_name; see _parse_quantity_g)."""
    m = re.search(r"([\d.]+)\s*dozen", s)
    if m:
        return float(m.group(1)) * _DOZEN
    m = re.search(r"([\d.]+)\s*(pcs?|pieces?|units?|nos?)\b", s)
    if m:
        return float(m.group(1))
    return None
```

### 2. Fix the count branch to cross-reference `item_name`

```python
# Count × known food-specific unit weight. "How many" comes from qty_desc
# (via _parse_count); "what food it's counting" comes from item_name — two
# separate strings that were never cross-referenced before (see
# tasks/features/nutrition-quantity-parsing.md). Mirrors the pattern
# _liquid_density_for already uses correctly, one branch over — but
# word-boundary matched, not raw substring: plain `in` would let "egg"
# match inside "Eggless" (Eggless Mayonnaise, Eggless Cake — a common
# Indian grocery naming convention), attributing egg weight to a product
# that contains no eggs. Same convention already established twice this
# session for the identical reason (pantry page's ITEM_ICON_KEYWORDS,
# the non-food gate's _NON_FOOD_PATTERN). The "(e?s)?" isn't optional
# decoration — a bare \bnoun\b would fail to match "eggs" (no boundary
# between "egg" and a following "s", both word characters), silently
# breaking the exact demo-catalog case this PRD exists to fix. "(e?s)?"
# also happens to produce the correct irregular plural for "potato"/
# "tomato"/"chilli" (→ "potatoes"/"tomatoes"/"chillies") via its "es"
# branch, matching the pantry page's own per-word handling of the same
# irregulars (tomato(es)?) without needing a separate rule per noun.
count = _parse_count(s)
if count is not None:
    name_lower = item_name.lower()
    for noun, weight in _UNIT_WEIGHTS_G.items():
        if re.search(rf"\b{re.escape(noun)}(e?s)?\b", name_lower):
            return count * weight
    # Count is known, but no per-unit weight for this specific food —
    # stay unresolvable rather than guess. A confidently wrong number is
    # worse than an honest miss (Goal 4).
    return None
```

This alone fixes both proven-broken demo-catalog cases: `"Farm Fresh Eggs"` / `"6 pcs"` → `"egg"` found in `item_name` → `6 × 55.0 = 330g`. `"Banana"` / `"1 dozen"` → `1 × 12 = 12`, `"banana"` found in `item_name` → `12 × 120.0 = 1440g`.

### 3. Multipack detection, guarded against silent miscounts

Must run **before** the existing plain-weight/volume checks — those would otherwise match just the trailing weight and silently drop the multiplier (the "200g instead of 400g" bug traced in Root Cause).

```python
# Multipack: "2 x 200 g", "3x1kg". Must run first — the plain weight/
# volume checks below would otherwise match just the trailing number and
# silently return the single-pack weight, not the true total.
m = re.search(r"([\d.]+)\s*x\s*([\d.]+)\s*(kg|kilogram|g|gm|gram|grams|l|litre|liter|liters|ml)", s)
if m:
    pack_count = float(m.group(1))
    single_g = _parse_weight_or_volume(f"{m.group(2)}{m.group(3)}", item_name)
    return pack_count * single_g if single_g is not None else None

# Something shaped like a multipack ("N x M") that isn't a weight/volume
# multipack we recognize — e.g. "2 x 6 pcs" (out of scope, see PRD). Bail
# out here rather than falling through to the plain weight or count
# branches below, which would misparse the second number on its own and
# silently drop the "2 x" multiplier.
if re.search(r"[\d.]+\s*x\s*[\d.]+", s):
    return None
```

`_parse_weight_or_volume` is today's existing plain-weight/volume logic (`nutrition_resolution.py:108-127`), unchanged in behavior, just factored out into its own function so both the multipack branch and the top-level plain-quantity path can call it — no duplicated conversion logic between the two:

```python
def _parse_weight_or_volume(s: str, item_name: str) -> float | None:
    """Today's existing weight/volume parsing (nutrition_resolution.py:108-127),
    extracted verbatim so the multipack branch can reuse it on a
    reconstructed "number+unit" substring instead of duplicating the
    kg/g/l/ml conversion logic a second time."""
    m = re.search(r"([\d.]+)\s*(kg|kilogram)", s)
    if m:
        return float(m.group(1)) * 1000

    m = re.search(r"([\d.]+)\s*(g|gm|gram|grams)", s)
    if m:
        return float(m.group(1))

    m = re.search(r"([\d.]+)\s*(l|litre|liter|liters)", s)
    if m:
        ml = float(m.group(1)) * 1000
        return ml * _liquid_density_for(item_name)

    m = re.search(r"([\d.]+)\s*ml", s)
    if m:
        ml = float(m.group(1))
        return ml * _liquid_density_for(item_name)

    return None
```

Note the multipack branch calls this with a *reconstructed* substring (`f"{m.group(2)}{m.group(3)}"`, e.g. `"200g"`), not the original `s` — so its own internal `re.search` calls re-run against that clean substring, not the full `"2 x 200 g"` string (which would let the kg/g regexes above see the leading `"2"` and misfire the same way the top-level call would without the multipack guard).

### 4. Full resulting structure of `_parse_quantity_g`

```python
def _parse_quantity_g(qty_desc: str, item_name: str) -> float | None:
    if not qty_desc:
        return None
    s = qty_desc.lower().strip()

    # 1. Multipack (Design §3) — must run before plain weight/volume.
    m = re.search(r"([\d.]+)\s*x\s*([\d.]+)\s*(kg|kilogram|g|gm|gram|grams|l|litre|liter|liters|ml)", s)
    if m:
        pack_count = float(m.group(1))
        single_g = _parse_weight_or_volume(f"{m.group(2)}{m.group(3)}", item_name)
        return pack_count * single_g if single_g is not None else None
    if re.search(r"[\d.]+\s*x\s*[\d.]+", s):
        return None  # unrecognized multipack shape — fail safe, don't guess

    # 2. Plain weight/volume (today's existing logic, factored out).
    single_g = _parse_weight_or_volume(s, item_name)
    if single_g is not None:
        return single_g

    # 3. Count × food-specific unit weight (Design §1-2, the actual fix).
    count = _parse_count(s)
    if count is not None:
        name_lower = item_name.lower()
        for noun, weight in _UNIT_WEIGHTS_G.items():
            if re.search(rf"\b{re.escape(noun)}(e?s)?\b", name_lower):
                return count * weight
        return None

    return None
```

---

## Worked Examples

**"Farm Fresh Eggs", "6 pcs" (demo catalog, currently broken):**
No multipack shape. `_parse_weight_or_volume("6.0 pcs", ...)` → `None` (no weight/volume unit). `_parse_count("6.0 pcs")` → `6`. `\begg(e?s)?\b` matches the plural `"eggs"` in `"farm fresh eggs"` → `6 × 55.0 = 330g`. Was `None` before this fix. (A bare `\begg\b`, with no plural handling, would *not* match "eggs" — this worked example only holds with the `(e?s)?` suffix.)

**"Eggless Mayonnaise", "1 pcs" (regression check — must stay unresolvable, not silently wrong):**
`\begg(e?s)?\b` against `"eggless mayonnaise"` — after matching "egg", the optional plural group can't consume "less" (not "e" or "s"), and there's no word boundary between "egg" and "less" (both word characters) for the zero-match case either → no match anywhere in `_UNIT_WEIGHTS_G`. Falls through to `return None` — stays unresolvable, does **not** attribute egg weight to an egg-free product.

**"Banana", "1 dozen" (demo catalog, currently broken):**
`_parse_count("1 dozen")` → `12`. `"banana"` found in `"banana"` → `12 × 120.0 = 1440g`. Was `None` before this fix.

**"Yakult", "2 x 200 g" (hypothetical multipack, not in current fixtures):**
Multipack regex matches: `pack_count=2`, `single_g = _parse_weight_or_volume("200g", "Yakult") = 200`. Returns `400g`. Was silently `200g` before this fix.

**"Kellogg's Muesli", "2 x 6 pcs" (deliberately out of scope):**
Multipack-weight/volume regex doesn't match (`"pcs"` isn't in the unit alternation). Generic `"N x M"` guard fires → returns `None`. Stays unresolvable, same as today — does **not** silently parse as `6` (ignoring the `"2 x"`), which is the failure mode this guard exists to prevent.

**"Amul Toned Milk", "1 L" (regression check — must be unaffected):**
No `"x"` in the string. `_parse_weight_or_volume` matches the volume branch, applies `_liquid_density_for("Amul Toned Milk") = 1.03` → `1030g`. Unchanged from today.

---

## Testing Plan

**Unit tests** (new, `tests/unit/test_nutrition_quantity_parsing.py` — no existing coverage for this function at all):
- The two proven-broken demo-catalog cases: eggs (`"6 pcs"`, plural item name) and banana (`"1 dozen"`) now resolve correctly.
- **Word-boundary + plural correctness — the case the review round caught:** `"Eggless Mayonnaise"` with a bare-count quantity stays unresolvable, not `6 × 55g`. This must be a real test, not just covered by inspection — it's the one property in this PRD with zero coverage from the demo catalog today (confirmed zero `_UNIT_WEIGHTS_G` collisions exist in current fixtures), so nothing here would fail visibly without one.
- Irregular plurals resolve via the food-noun table: `"Baby Potatoes"`, `"Roma Tomatoes"`, `"Green Chillies"` all match their singular table entries (`potato`, `tomato`, `green chilli`) via the `(e?s)?` suffix.
- Multipack: `"2 x 200 g"`, `"3x1kg"` variants (with/without spaces, `x`/`X`) compute the true total, not the single-pack weight.
- Multipack guard: `"2 x 6 pcs"` returns `None`, not `6`.
- Count with unknown food noun (e.g. `"6 pcs"` on an item whose name matches nothing in `_UNIT_WEIGHTS_G`) returns `None`, not a guessed weight.
- Regression: existing weight (`"5 kg"`), volume (`"1 L"`, with density), and already-working count cases (if any) all produce identical output to before the change.
- `_parse_count` in isolation: `"dozen"`, `"pcs"`/`"piece"`/`"pieces"`/`"unit"`/`"units"`/`"nos"` all parse; non-matching strings return `None`.

**Regression check:** re-run `test_nutrition_resolution_gap_to_cart.py`, `test_nutrition_gaps.py`, `test_nutrition_consumption_scaling.py`, and `test_nutrition_non_food_gate.py` after implementation — several of those tests construct `resolved` dicts with a pre-set `quantity_g` (bypassing this function entirely), but any that exercise `resolve_item()`/`_parse_quantity_g` end-to-end should be checked for incidental assumptions about today's (broken) count-parsing behavior.

## Rollout Notes

- Forward-only, same as both companion PRDs: already-cached `NutritionCache` rows with `quantity_unresolvable=True` are not retroactively re-parsed. A follow-up task could re-resolve rows where `quantity_unresolvable=True` and the item name matches a now-supported pattern, once this has been live for a while.
- `_UNIT_WEIGHTS_G`'s vocabulary is a living list (same as `_NON_FOOD_KEYWORDS` in the companion PRD) — extend it as specific count-based foods are found still resolving to `None` in practice.
