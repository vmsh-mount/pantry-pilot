# PRD — Surface Partial Data for Coverage-Gated Nutrients, Instead of Hiding It

**Status:** Ready for implementation
**Date:** 2026-08-02
**Related:** [`nutrition-gap-to-cart-phase-b3-gap-detection.md`](nutrition-gap-to-cart-phase-b3-gap-detection.md) (the coverage guard this amends), [`nutrition-non-food-gate.md`](nutrition-non-food-gate.md) / [`nutrition-consumed-not-purchased.md`](nutrition-consumed-not-purchased.md) (same "disclose confidence, don't smooth over" principle this brings the coverage guard in line with)

---

## Problem

When a nutrient's weekly coverage falls below 60%, `compute_gaps` returns `status: "insufficient_data"` and nothing else usable — no figure, no partial signal. But the actual weekly total *was* computed on the way there; it's thrown away, not because it's wrong, but because it didn't clear a confidence bar. Every other confidence-sensitive number in this product (per-item calorie estimates, the LLM-estimate badge, the non-food gate's `"not_food"` state) is handled the opposite way: show the number, disclose the confidence, let the person decide. The coverage guard is the one place in the nutrition feature that hides instead of discloses.

This isn't just a backend shape issue — it's a three-consumer blind spot, and the frontend one is worse than the backend even requires:

1. **Backend** discards the computed `actual` value entirely for `insufficient_data` gaps.
2. **`NutritionGapsCard.tsx`** (dashboard card) doesn't even render the `coverage` percentage the backend *does* already send — the micronutrient chip renders a flat `"Iron — no data"` regardless of whether coverage is 0% or 55%.
3. **`/nutrition/gaps/page.tsx`** (the dedicated "Close your gaps" screen) filters to `status === "short"` only — a nutrient that couldn't be checked doesn't just show as uncertain, it doesn't appear at all, with no footnote explaining the absence.

## Root Cause

`nutrition_gaps.py:136-140` (target-based: calories/protein/fiber):

```python
for nutrient, (target_key, unit) in target_map.items():
    actual, coverage = _weekly_actual_and_coverage(items, nutrient)
    if coverage < COVERAGE_THRESHOLD:
        gaps.append({"nutrient": nutrient, "status": "insufficient_data", "coverage": round(coverage, 2)})
        continue
```

`nutrition_gaps.py:159-163` (watch-list: b12/iron), identical pattern:

```python
for nutrient in _WATCH_NUTRIENTS:
    actual, coverage = _weekly_actual_and_coverage(items, nutrient)
    if coverage < COVERAGE_THRESHOLD:
        gaps.append({"nutrient": nutrient, "status": "insufficient_data", "coverage": round(coverage, 2)})
        continue
```

Both call sites unpack `actual` from `_weekly_actual_and_coverage` and never reference it again on the `insufficient_data` path — it's a local variable that goes out of scope. `coverage` at least makes it into the response dict; `actual` doesn't reach the dict at all.

**Frontend confirms it's not read even where it's sent** — `NutritionGapsCard.tsx:265-274`:

```tsx
{microGaps.map((g) => (
  <span key={g.nutrient} ...>
    ...{NUTRIENT_LABEL[g.nutrient] ?? g.nutrient} — no data
  </span>
))}
```

`g.coverage` is never referenced in this block. The `NutritionGap` TS type (`api.ts:225-235`) already has `actual_weekly?: number | null` as an optional field, currently populated only on the `"short"` path — the frontend contract already anticipates this value existing on other statuses, it's just never been wired up on this one.

**Third consumer, `nutrition/gaps/page.tsx:202`:**

```tsx
const shortGaps = gaps.filter((g) => g.status === "short" && g.recommendations && g.recommendations.length > 0)
```

`insufficient_data` gaps are filtered out before rendering — not shown, not footnoted.

**A fourth, pre-existing issue surfaces once the chip cluster actually renders partial data:** `microGaps` (`NutritionGapsCard.tsx:171`) filters the micronutrient chip cluster by nutrient name only, not status — so a *confirmed* zero-intake b12/iron result (`status: "short"`, `watch_reason: "no_source_in_window"`, `nutrition_gaps.py:164-171`) lands in the same list as genuinely unresolved ones. This isn't introduced by this PRD, but it's the exact class of bug this PRD exists to fix, and left alone it produces a direct on-card contradiction once the chip cluster starts rendering distinguishable states (see [Design §3](#3-frontend--dashboard-card-micronutrient-chips)) — the card's own summary line would say "B12 missing" while the chip two sections down says "B12 — no data," about the same result.

## The Legitimate Concern Behind the Original Design — and Why It Points to "Disclose," Not "Hide"

The B3 PRD's coverage guard exists for a real reason: *"A household whose week resolved mostly via OFF/USDA has no B12 data — that is 'no data,' not 'no intake.'"* A bare `0mg iron` for a vegetarian household could misread as a genuine deficiency signal when it's actually a measurement gap. That's a legitimate risk, worth taking seriously for a diet-adjacent metric.

But this product already has a solved pattern for exactly this risk: a per-item confidence badge next to a real number, not a hidden number. An LLM-estimated calorie count is never withheld pending "enough confidence" — it's shown, visibly marked as an estimate. Applying the same logic here means the fix isn't to keep hiding the figure, it's to show it with language that makes the uncertainty impossible to miss — closer to *"~12g protein (from 45% of this week's orders — likely higher)"* than to a bare number presented as fact.

## Goals

1. Backend: include `actual_weekly` in the `insufficient_data` gap dict on both paths (target-based and watch-list) — reuse the field name already in the type, no new field needed.
2. Distinguish "zero data" from "some data, just not enough to be confident" — a coverage of exactly `0.0` means nothing was resolved for this nutrient at all (no partial signal exists); anything between `0` and the `0.6` threshold means a real, if shaky, partial figure exists and should be shown.
3. Frontend dashboard card: micronutrient chips show the partial figure + coverage with explicit uncertainty framing when one exists, not a flat "no data" that can't tell 0% from 55%.
4. Frontend "Close your gaps" screen: a low-key disclosure footnote for nutrients that couldn't be checked, so their absence from the actionable list has a stated reason instead of just not appearing.
5. **Do not lower the bar for what's actionable.** Recommendations stay gated behind `status == "short"` (`api/nutrition.py:321-324`, unchanged) — `insufficient_data` nutrients never get `get_recommendations_for_nutrient` called for them, before or after this change. This is a disclosure fix, not a confidence-threshold change; nothing about when Gap-to-Cart suggests a purchase is touched.
6. Fix `microGaps`' missing status filter (pre-existing, but must land alongside Goal 3 or the chip cluster contradicts the same card's summary line for a confirmed-zero watch-list result) — see [Design §3](#3-frontend--dashboard-card-micronutrient-chips).

## Out of Scope

- **Changing `COVERAGE_THRESHOLD` itself.** The 60% bar for what counts as *actionable* stays exactly where it is — see Goal 5.
- **`MACRO_ROWS` in `NutritionGapsCard.tsx`.** Those dot-bars are fed by a separate `week` prop, a different data path entirely (not `compute_gaps`'s output) — they already show the raw total regardless of coverage today. That's a genuinely different inconsistency (shows without a caveat, rather than hides without one) — worth flagging, not fixing here; folding it in would blur two distinct problems into one change.
- **Adding `coverage`/partial framing to already-`"short"` gaps.** Those already clear the 60% bar and show a real target-vs-actual diff; there's a case for surfacing coverage there too for full transparency, but it's an adjacent enhancement, not what this PRD's problem statement is about.
- **Any change to `_weekly_actual_and_coverage`'s math.** The coverage/actual computation itself is correct; only what happens to the result afterward changes.

---

## Design

### 1. Backend — stop discarding `actual`

`nutrition_gaps.py`, target-based path:

```python
for nutrient, (target_key, unit) in target_map.items():
    actual, coverage = _weekly_actual_and_coverage(items, nutrient)
    if coverage < COVERAGE_THRESHOLD:
        gaps.append({
            "nutrient": nutrient, "status": "insufficient_data",
            "coverage": round(coverage, 2),
            # None when truly zero signal exists (coverage == 0 — nothing
            # resolved for this nutrient at all); a real partial figure
            # otherwise. The frontend uses this distinction directly rather
            # than re-deriving it from coverage == 0.
            "actual_weekly": round(actual, 1) if coverage > 0 else None,
            "unit": unit,
        })
        continue
```

Watch-list path, same shape:

```python
for nutrient in _WATCH_NUTRIENTS:
    actual, coverage = _weekly_actual_and_coverage(items, nutrient)
    if coverage < COVERAGE_THRESHOLD:
        gaps.append({
            "nutrient": nutrient, "status": "insufficient_data",
            "coverage": round(coverage, 2),
            "actual_weekly": round(actual, 1) if coverage > 0 else None,
            "unit": _UNITS[nutrient],
        })
        continue
```

`unit` is added to both — today's `insufficient_data` dict doesn't carry it (only the `"short"` dict does), so a frontend showing a bare number would have no unit to attach to it.

### 2. Frontend type — no change needed

`NutritionGap` (`api.ts:225-235`) already has `actual_weekly?: number | null` and `unit?: string` as optional fields, populated today only on `"short"`. This PRD populates them on `"insufficient_data"` too — same fields, same type, no schema change.

### 3. Frontend — dashboard card micronutrient chips

**Pre-existing bug this PRD must fix first, or the chip logic below contradicts itself:** `microGaps` (`NutritionGapsCard.tsx:171`) filters only by nutrient name, not status:

```tsx
const microGaps = gaps.filter((g) => WATCH_NUTRIENTS.includes(g.nutrient))
```

The watch-list backend path can emit a b12/iron gap with `status: "short"` and `watch_reason: "no_source_in_window"` (`nutrition_gaps.py:164-171`) — coverage cleared 60%, and the household's intake is *confirmed* zero, not unmeasured. That's a materially different, more confident state than `"insufficient_data"`, and it's already represented correctly elsewhere on the same card — `summaryLine()` (`NutritionGapsCard.tsx:17-20`) renders a `"short"` gap with `short_by == null` (exactly what the watch-list sets) as `"{label} missing"`. Without a status filter, that same gap *also* lands in `microGaps` below, and under the chip logic in this PRD would render `"— no data"` for it — one card asserting "confirmed missing" in one section and "we don't know" in the next. The chip cluster's own copy (`"shown as watch-only until we can verify"`) says its job is to represent *unresolved* watch nutrients specifically — so it needs a status filter to actually do only that:

```tsx
const microGaps = gaps.filter((g) => WATCH_NUTRIENTS.includes(g.nutrient) && g.status === "insufficient_data")
```

A confirmed `"short"` b12/iron gap then surfaces exactly once — through `summaryLine()` and the `fixableGaps`/recommendations path (both already status-filtered correctly, unaffected by this change) — instead of a second, contradictory time in the chip cluster.

With that fixed, every gap remaining in `microGaps` is guaranteed `status === "insufficient_data"`, so the chip rendering only needs to distinguish coverage `0` from partial coverage, not `"short"` from `"insufficient_data"` (that split already happened at the filter):

```tsx
{microGaps.map((g) => {
  const label = NUTRIENT_LABEL[g.nutrient] ?? g.nutrient
  const hasPartialSignal = g.actual_weekly != null
  return (
    <span key={g.nutrient} ...>
      <span className="w-[5px] h-[5px] rounded-full flex-shrink-0" style={{ background: "#9CA3AF" }} />
      {hasPartialSignal
        ? `${label} — ~${Math.round(g.actual_weekly!)}${g.unit ?? ""} (${Math.round((g.coverage ?? 0) * 100)}% checked)`
        : `${label} — no data`}
    </span>
  )
})}
```

The `~` prefix and "(N% checked)" suffix do the same job the `~AI est.` prefix already does for per-item confidence elsewhere in this product — visibly mark the number as uncertain without hiding it. The existing explanatory copy below the chips (`"No numeric target for these yet — shown as watch-only..."`) stays as-is; it's still accurate for both remaining states (zero coverage vs. partial coverage) now that a confirmed-zero result no longer reaches this list at all.

### 4. Frontend — "Close your gaps" screen footnote

`nutrition/gaps/page.tsx`, alongside the existing `shortGaps` filter:

```tsx
const shortGaps = gaps.filter((g) => g.status === "short" && g.recommendations && g.recommendations.length > 0)
const uncheckedGaps = gaps.filter((g) => g.status === "insufficient_data")
```

Render a single low-key line when `uncheckedGaps.length > 0`, near the top of the page (not per-card — this is a page-level disclosure, not tied to any specific recommendation card):

```tsx
{uncheckedGaps.length > 0 && (
  <p className="text-[11px] text-gray-400 px-1 mb-3">
    {uncheckedGaps.map((g) => NUTRIENT_LABEL[g.nutrient] ?? g.nutrient).join(", ")}
    {" "}couldn&apos;t be checked yet — not enough of this week&apos;s orders resolved.
  </p>
)}
```

No per-nutrient partial figure needed here — this page's job is the actionable list; the dashboard card is where the partial-figure disclosure lives. This is just enough to explain an absence, not a second place to duplicate the same figure.

---

## Worked Example

**Household orders 5 items this week; only 2 resolved with iron data present, at 3mg and 1mg:**

`_weekly_actual_and_coverage(items, "iron")` → `resolved_items` has (say) 4 entries (1 unresolved, excluded), 2 of those 4 have a non-null iron value → `coverage = 2/4 = 0.5`, `actual = 4.0`.

**Before this fix:** `{"nutrient": "iron", "status": "insufficient_data", "coverage": 0.5}`. Dashboard chip: `"Iron — no data"`. Gaps page: iron doesn't appear anywhere.

**After this fix:** `{"nutrient": "iron", "status": "insufficient_data", "coverage": 0.5, "actual_weekly": 4.0, "unit": "mg"}`. Dashboard chip: `"Iron — ~4mg (50% checked)"`. Gaps page footnote: `"Iron couldn't be checked yet — not enough of this week's orders resolved."` Recommendations are still not computed for iron — `status` is still `"insufficient_data"`, not `"short"`, so `api/nutrition.py`'s `if gap["status"] == "short"` gate is untouched and nothing new gets suggested for purchase.

**Household orders nothing with any iron resolution this week (coverage genuinely 0):**

`actual_weekly` stays `None` (the `if coverage > 0 else None` guard) — chip stays `"Iron — no data"`, correctly distinct from the 50%-coverage case above.

**Vegetarian household, coverage clears 60% for b12, actual resolves to a confirmed zero:**

`compute_gaps` emits `{"nutrient": "b12", "status": "short", "target_weekly": None, "actual_weekly": 0.0, "short_by": None, "unit": "mcg", "watch_reason": "no_source_in_window"}`. **Before the `microGaps` fix:** summary line reads `"B12 missing"`; chip cluster, two sections down on the same card, reads `"B12 — no data"` — same result, contradictory framing. **After the fix:** `microGaps`' `status === "insufficient_data"` filter excludes this gap entirely (it's `"short"`, not `"insufficient_data"`) — only the summary line (and, if it has recommendations, the "Fix these in my cart" row) represents it. No second, contradictory appearance.

---

## Testing Plan

**Unit tests** (extend `tests/integration/test_nutrition_gaps.py`, which already has direct `compute_gaps` coverage for the coverage guard):
- Coverage between 0 and 60%: `insufficient_data` gap includes `actual_weekly` (non-null, matching the computed sum) and `unit`.
- Coverage exactly 0 (no resolved items carry this nutrient at all): `actual_weekly` is `None`, not `0.0` — the two must stay visibly distinct.
- Recommendations are still never attached to an `insufficient_data` gap regardless of whether `actual_weekly` is now present — `api/nutrition.py`'s `status == "short"` gate is unchanged; a test asserting this explicitly protects Goal 5 from a future regression.
- Both target-based (protein/fiber/calories) and watch-list (b12/iron) paths covered — they're separate code blocks in `compute_gaps`, not shared logic.

**Regression check:** re-run the existing `test_b12_low_coverage_returns_insufficient_data_not_gap` and `test_non_food_item_does_not_dilute_coverage` tests — both assert on `insufficient_data` gap shape today (e.g. `assert "short_by" not in b12_gap`) and should still pass with the two new keys added, since they only assert absence of unrelated keys, not an exhaustive dict shape.

**Frontend:** no existing test infra for these two components in this codebase (confirmed — no `.test.tsx` files under `nutrition/`) — verify visually via the dev server: a household with partial iron/b12 coverage shows the caveated figure on the dashboard card, the "Close your gaps" screen shows the footnote when at least one nutrient is `insufficient_data`, and — the specific case the review round caught — a vegetarian household with a confirmed-zero (`status: "short"`, `watch_reason: "no_source_in_window"`) b12 or iron result shows `"{label} missing"` in the summary line and does **not** also appear in the micronutrient chip cluster below it.

## Rollout Notes

- Pure response-shape addition (two new optional keys on an existing dict) plus display logic — no schema migration, no data backfill. Every `insufficient_data` gap computed from the moment this ships forward carries the new fields; nothing is stored, so there's no "old rows still show the old shape" concern the way the pack-vs-consumed and non-food-gate PRDs had to account for.
