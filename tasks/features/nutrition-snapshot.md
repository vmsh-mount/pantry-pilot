# PRD — Nutrition Snapshot Redesign

**Status:** Implemented
**Date:** 2026-07-28
**Branch:** `feature/nutrition-snapshot`
**Mockup:** [`tasks/features/nutrition-snapshot-mockup.html`](./nutrition-snapshot-mockup.html) — approved

---

## Problem

`NutritionCard` (shown as "🌿 Nutrition snapshot" on Orders, Runs, Quick, and Routine-detail pages) renders every macro as a progress bar against a target — but the target is wrong:

```ts
// current NutritionCard.tsx, line 121
const targets = { calories: 14000, protein_g: 350, carbs_g: 3920, fat_g: 770, fiber_g: 175, sodium_mg: 16100 }
```

These are hardcoded ICMR **weekly** defaults for **one person**. `NutritionCard` shows nutrition for **a single order**. Plotting one order's protein (e.g. 120g) against a weekly target (350g) renders a permanently near-empty bar — every macro, on every order, regardless of how balanced it actually was. The visualization isn't stylistically bad, it's answering a question ("how much of your week does this order cover?") nobody asked, with a number that will always look bad.

Compounding it: the component uses raw Tailwind `gray-900`/`dark:` utility classes and arbitrary macro colors, disconnected from the rest of the app's design language.

---

## Goals

- Replace the "progress toward a target" framing with **composition** — what this order's calories are actually made of — which is a coherent question for a single order in isolation and needs no external target.
- Restyle to the app's actual palette and card language.
- Declutter the per-item list (confidence badges currently outweigh the item name itself).

## Non-Goals

- **`MacroBar` is not changed and not removed.** It's correctly reused by [`nutrition/weekly/page.tsx`](../../app/cockpit/src/app/nutrition/weekly/page.tsx), which compares real weekly actuals against real personalised weekly targets — that pairing is correct and stays exactly as-is.
- **`ConfidenceBadge` is not changed and not removed.** It's reused by [`nutrition/gaps/page.tsx`](../../app/cockpit/src/app/nutrition/gaps/page.tsx) for a different context where the fuller badge is still appropriate. `NutritionCard`'s own item list switches to a new, local-only dot indicator instead — it does not touch this shared export.
- No backend changes. `OrderNutrition`'s shape, the polling logic, loading/error states, and the "computing…" retry behavior are all unchanged.
- No change to where `NutritionCard` is embedded (Orders, Runs, Quick, Routines) or its `orderId` prop contract.

---

## Design

### Calorie hero — unchanged in spirit, restyled

Same big number, same "kcal this order" caption, recolored from the arbitrary `#C45E18` to sit correctly against the app's ink/orange system.

### Composition bar — replaces the Protein/Carbs/Fat `MacroBar` rows

A single segmented horizontal bar: protein/carbs/fat as a share of **this order's own calories**, computed via the standard Atwater factors (protein 4 kcal/g, carbs 4 kcal/g, fat 9 kcal/g) — not against `total_calories`, but normalized against the **sum of the three macro-derived calorie values**, so the bar always sums to exactly 100% regardless of any rounding drift between how `total_calories` and the per-macro grams were independently resolved:

```
protein_kcal = (total_protein_g ?? 0) × 4
carbs_kcal   = (total_carbs_g   ?? 0) × 4
fat_kcal     = (total_fat_g     ?? 0) × 9
macro_kcal_sum = protein_kcal + carbs_kcal + fat_kcal

pct_protein = macro_kcal_sum > 0 ? protein_kcal / macro_kcal_sum : 0
pct_carbs   = macro_kcal_sum > 0 ? carbs_kcal   / macro_kcal_sum : 0
pct_fat     = macro_kcal_sum > 0 ? fat_kcal     / macro_kcal_sum : 0
```

- Any of the three grams being `null` is treated as `0` contribution — the segment just doesn't appear, it doesn't break the calculation.
- If `macro_kcal_sum` is `0` (nothing resolved enough to have any macro data), the composition bar and its legend are omitted entirely — same spirit as the component's existing "nothing resolved → error state" handling.
- Legend below the bar shows each macro's color dot, grams, and percentage (`120g · 32%`).

### Fiber & Sodium — plain stat tiles, not bars

Two side-by-side tiles showing absolute values (`fmtQty`-style formatting, `—` if null). Neither fiber nor sodium has a coherent "share of this order's calories" reading, and comparing either to a weekly ceiling reintroduces the exact wrong-denominator problem this PRD exists to fix. Absolute numbers are the honest choice here.

### Per-item list — confidence icon, not badge

Replace the boxed `ConfidenceBadge` in the item row with a small **icon** (not a color dot), reducing visual weight so the item name reads first while staying more legible than an unlabeled dot.

**`NutritionConfidence` is a 5-state type, not 4** (`verified | high | medium | estimate | unresolved` — confirmed against the backend's confidence ladder in `nutrition_resolution.py`, where `_rank = {"verified": 4, "high": 3, "medium": 2, "estimate": 1}`). An earlier draft of this table conflated `high` and `medium` into one "database match" bucket, which would have silently dropped a real confidence tier — `high` (Open Food Facts, strong barcode match) and `medium` (Open Food Facts weak match, or USDA) are genuinely different trust levels. Corrected, one glyph per state:

| State | Icon | Color | Meaning |
|---|---|---|---|
| `verified` | check mark | `T.green` | Confirmed accurate |
| `high` | check-in-circle | `#4A7FA5` | Strong match from Open Food Facts |
| `medium` | box/package glyph | `T.ink3` (`#8E8E93`) | Weaker database match — Open Food Facts or USDA |
| `estimate` | sparkles glyph | `#D9A24E` | AI estimate — no direct match found |
| `unresolved` | dash | `#D0D0D0` | No nutrition data available |

`estimate` rows keep the existing italic treatment. This is a new, local-only element — not a change to the shared `ConfidenceBadge` export.

**Label via native hover, not a persistent legend.** Each icon carries a native `title` attribute with the full label (e.g. `"Verified — from the product label"`), shown on hover — no separate legend row needed since the explanation lives on the icon itself. **Caveat, explicit because the app is mobile-only:** hover isn't a reliable interaction on touch devices. `title` is the pragmatic choice anyway — it's zero-JS, accessible, degrades to long-press on most mobile browsers, and doesn't need a custom tooltip component built and maintained for a single card's minor affordance. If in practice this proves genuinely undiscoverable on mobile, the fallback is the one-time text legend from the previous draft of this PRD, not a bigger tooltip system.

---

## Component Changes

All changes are in **`app/cockpit/src/components/nutrition/NutritionCard.tsx`**.

- `ConfidenceBadge` and `MacroBar` — **kept, exported, unchanged** (external consumers depend on them as-is).
- Remove the hardcoded `targets` object and its five `<MacroBar>` calls inside `NutritionCard`'s loaded state.
- New local `CompositionBar` component implementing the calculation above, styled per the mockup (segmented bar + dot-legend row).
- New local `StatTile` component for Fiber/Sodium (or inline markup — implementer's call, doesn't need to be its own exported component since nothing else uses it).
- New local `ConfidenceIcon` (small inline SVG per state, per the table above) replacing `ConfidenceBadge` inside this file's own `ItemRow`, each with a `title` attribute carrying the full label text.
- Recolor the calorie hero number and restyle the card chrome (header, borders, footer) to the app's palette — no more `dark:` Tailwind classes (the app doesn't have a dark mode anywhere else).

`OrderNutrition` polling logic, loading/error states, and the footer ("N of M items resolved", "Report incorrect data") are unchanged.

---

## Acceptance Criteria

- No macro on the snapshot is ever plotted against a weekly household target — the composition bar has no external target at all.
- Composition bar segments always sum to 100% width (verify with an order where `total_calories` and the per-macro grams don't perfectly reconcile — the bar must not overflow or leave a gap).
- An order with zero resolved macro data shows no composition bar (not a bar stuck at 0%).
- Fiber and Sodium render as plain figures, no bars.
- Every confidence icon has a `title` attribute with its full label text — no icon relies on color alone to communicate its meaning.
- `nutrition/weekly/page.tsx`'s use of `MacroBar` is visually and behaviorally unchanged — still weekly-actual vs. weekly-target, still the same two calls.
- `nutrition/gaps/page.tsx`'s use of `ConfidenceBadge` is visually and behaviorally unchanged.
- No `dark:` Tailwind classes remain in `NutritionCard.tsx`.
- `npx tsc --noEmit` clean.

---

## Files to Change

| File | Change |
|---|---|
| `app/cockpit/src/components/nutrition/NutritionCard.tsx` | Remove hardcoded weekly `targets` + per-order `MacroBar` calls; add `CompositionBar`, stat tiles, `ConfidenceDot`; restyle to app palette. `ConfidenceBadge`/`MacroBar` exports untouched. |

No backend changes. No other files touch `NutritionCard`'s internals — `nutrition/weekly/page.tsx` and `nutrition/gaps/page.tsx` only import the two exports that aren't changing.
