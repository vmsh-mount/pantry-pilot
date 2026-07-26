# PRD — Dashboard Nutrition Unification

**Status:** Ready for implementation
**Date:** 2026-07-26
**Branch:** `feature/nutrition-unification`
**Mockup:** [`tasks/features/nutrition-unification-mockup.html`](./nutrition-unification-mockup.html) — v3, approved after 4 rounds of revision

---

## Problem

The dashboard has **three separate nutrition surfaces** stacked back to back:

1. Hero calorie badge — calories only, no other macros.
2. [`NutritionGapsCard`](../../app/cockpit/src/components/nutrition/NutritionGapsCard.tsx) — its own "Nutrition" header, a status badge ("needs attention" / "on track"), a one-line gap summary, and three navigation rows (Household targets, This week's report, Fix these in my cart).
3. A second, separately-labeled "Nutrition" eyebrow + macro dot-bar panel (Protein/Fiber/Sodium) directly below it, in [`dashboard/page.tsx`](../../app/cockpit/src/app/dashboard/page.tsx).

Protein and fiber numbers effectively appear twice. Two blocks both say "Nutrition." And the backend already tracks **iron and B12** as watch-list micronutrients ([`nutrition_gaps.py`](../../app/pilot/app/services/nutrition_gaps.py)) but never renders them — they're only mentioned in a buried text fragment like *"Iron 3mg short · B12 missing"*.

---

## Goals

- One "Nutrition" card, one visual language.
- Macro rows (Calories, Protein, Fiber, Sodium) always visible, full-size — same legibility as today's existing dot-bar panel. Nothing currently easy to read gets smaller or hidden.
- Navigation rows (Household targets / This week's report / Fix these in my cart) stay exactly as visible as they are today — never hidden behind a tap.
- Micronutrients (Iron, B12) become visible for the first time, behind a small, dedicated reveal — the only thing in the card that's collapsed by default, since it's the only genuinely new content and has no numeric target.

## Non-Goals

- No change to how gaps are computed, targets are personalised, or how `nutrition_gaps_enabled` is granted — this is a presentation-layer consolidation.
- No change to `/nutrition/weekly`, `/nutrition/gaps`, or `/settings/targets` pages themselves — the unified card still links out to them.
- No numeric targets added for iron/B12 — the backend has none today (`_WATCH_NUTRIENTS` in `nutrition_gaps.py` are watch-only by design); this PRD does not change that.
- No backend schema change. Both existing endpoints (`GET /v1/dashboard`, `GET /v1/nutrition/gaps`) are reused as-is.

---

## Design

### Card anatomy (top to bottom)

```
┌─────────────────────────────────────┐
│ 🌿 Nutrition          [needs attn.] │  ← header + status badge
├─────────────────────────────────────┤
│ ● Calories  ●●●●●○○   36.1k/50.1k   │
│ ● Protein   ●●●●●○○   1250/1837g    │  ← always visible,
│ ● Fiber     ●●●●○○○   102/175g      │    full-size dot-bar rows
│ ● Sodium    ●●●●●●●   61k/58.8k     │    (unchanged from today's panel)
├─────────────────────────────────────┤
│ Micronutrients (2)               ▾  │  ← light-gray strip (#F7F8F5),
├─────────────────────────────────────┤    collapsed by default
│ [Iron — no data] [B12 — no data]    │  ← revealed on tap, edge-to-edge
│ No numeric target for these yet…    │    fill (no rounded corners),
├─────────────────────────────────────┤    same #F7F8F5 tint
│ Household targets               ›   │
│ This week's full report         ›   │  ← always visible, unchanged
│ Fix these in my cart             ›  │    from today's gaps card
└─────────────────────────────────────┘
```

### Interaction

- Only the **"Micronutrients (N)"** strip is a toggle. Tapping it expands/collapses the Iron/B12 chip row in place (no navigation). The strip's own background is `#F7F8F5` while collapsed and reverts to white once expanded — it's a toggle affordance, not a permanent panel divider.
- The revealed micronutrient content sits in its own `#F7F8F5` band, flush edge-to-edge with the card (no border-radius, no inset margin) — top and bottom hairlines only, so it reads as one continuous surface rather than a floating box.
- Header status badge, macro rows, and nav rows never move, never require a tap.

### Micronutrient chip states

Iron/B12 have no numeric target (`_WATCH_NUTRIENTS` in `nutrition_gaps.py` always return `status: "insufficient_data"`), so they render as a **neutral chip**, not a colored good/bad bar:

| Gap API `status` | Chip |
|---|---|
| `insufficient_data` (no `watch_reason`, or coverage low) | `● Iron — no data` (gray dot, `chip-unknown` style) |
| Future: numeric target added | Not in scope — chip design doesn't need to change if this ships later, since it's visually neutral either way |

The strip label is `Micronutrients (N)` where N = count of watch-list nutrients present in the gaps response (today, always 2: iron, b12).

---

## Data flow — resolving the two-API-call problem

This is the one thing the mockup couldn't resolve with CSS: the macro rows and the gap/micronutrient content come from **two different endpoints with very different latency**.

| Content | Source | Latency |
|---|---|---|
| Header, macro rows (Calories/Protein/Fiber/Sodium) | `GET /v1/dashboard` (`data.week`) | Fast — already loaded for the rest of the page |
| Status badge, micronutrient chips, nav-row subtext (e.g. "2 items close the gap") | `GET /v1/nutrition/gaps` | Slow — 15–20s+ (live Swiggy searches per recommendation) |

**Decision: keep them as two calls, do not block on the slow one.** This matches the existing, deliberate pattern already in `NutritionGapsCard.tsx` (see the code comment there — the loading-state split was added specifically because gating the whole card on the gaps call blocked navigation for its full duration). The unified card continues that pattern:

1. Macro rows render immediately from the dashboard payload the page already fetched — no new request, no loading state for them.
2. Header status badge shows a `checking…` spinner state until `/nutrition/gaps` resolves, exactly as today.
3. The Micronutrients strip is present but shows `Micronutrients` with no count / disabled tap state until the gaps response lands, then updates to `Micronutrients (N)` and becomes interactive.
4. Nav-row subtext ("2 items close the gap") updates once resolved; the rows themselves are always rendered and always tappable (they don't require gap data to navigate).

No backend change needed to support this — `GET /v1/dashboard` and `GET /v1/nutrition/gaps` are both reused unmodified.

### Two independent visibility gates compose into one card

Today, the macro panel and the gaps card are gated independently:

- Macro panel: `data.week.has_nutrition_data` (real order data resolved this week)
- Gaps card: `nutrition_gaps_enabled` household flag (dark-launch), fetched via `GET /v1/settings`

The unified card must handle both gates correctly since they're no longer two separate components:

| `has_nutrition_data` | `nutrition_gaps_enabled` | Card shows |
|---|---|---|
| false | any | **Hidden entirely** — matches today (no macro panel, no gaps card, without consumption data there's nothing to show) |
| true | false | Header + macro rows only. No status badge, no Micronutrients strip, no nav rows — those are gated by the flag, same as `NutritionGapsCard` returning `null` today when the flag is off. |
| true | true | Full card as designed above. |

---

## Component Changes

### `components/nutrition/NutritionGapsCard.tsx` → becomes the unified card

Rename is optional (implementer's call — `NutritionCard` collides with the existing per-order component, so keep `NutritionGapsCard` or rename to e.g. `DashboardNutritionCard`). Changes:

- Accepts the dashboard's `week` data as a prop (macro values + targets + `has_nutrition_data`) — the parent (`dashboard/page.tsx`) already has this, no new fetch.
- Renders the header + status badge (existing logic, unchanged) + the four always-visible macro rows (moved in from `dashboard/page.tsx`, styling unchanged) + the Micronutrients toggle strip (new) + the three nav rows (existing, unchanged, now always rendered instead of conditionally appended).
- Micronutrient chips read from the existing `gaps` state already fetched in this component — filter for `nutrient` in the watch list (`iron`, `b12`) rather than the numeric-target ones (`calories`, `protein`, `fiber`), which the macro rows already cover.
- New local state: `microExpanded: boolean`, toggled by the strip tap. No new network calls.

### `app/dashboard/page.tsx`

- Remove the standalone macro dot-bar panel block (`NUTRITION_ROWS.map(...)`) and its `"Nutrition"` eyebrow label — this content moves into the unified card.
- Remove the `data.week.has_nutrition_data` conditional wrapper around that block — the unified card now owns its own visibility logic.
- Pass `week={data.week}` into the (renamed or same) `NutritionGapsCard` component.
- Hero calorie badge is **kept as-is** — confirmed acceptable despite the overlap with the card's own Calories row; it's the one number visible without scrolling into the body.

---

## Acceptance Criteria

- Dashboard shows exactly one "Nutrition"-labeled surface, not two.
- Macro rows (Calories, Protein, Fiber, Sodium) are visible immediately on page load whenever `has_nutrition_data` is true — no tap, no wait on the gaps call.
- Household targets / This week's report / Fix these in my cart rows are visible whenever `nutrition_gaps_enabled` is true — never behind a tap.
- Micronutrients (Iron, B12) are visible for the first time anywhere in the product, behind exactly one tap, rendered as neutral "no data" chips (not a fake percentage bar).
- The Micronutrients strip's light-gray background applies only while collapsed; the expanded chip content sits in its own full-width `#F7F8F5` band with no rounded corners.
- No new network requests introduced — `GET /v1/dashboard` and `GET /v1/nutrition/gaps` are both reused as-is, same call sites, same timing.
- `nutrition_gaps_enabled = false` still shows macro rows (matches today's macro-panel behavior, which isn't gated by that flag).

---

## Files to Change

| File | Change |
|---|---|
| `app/cockpit/src/components/nutrition/NutritionGapsCard.tsx` | Absorb macro rows + Micronutrients toggle strip; accept `week` prop; render nav rows unconditionally (already flag-gated by early return) |
| `app/cockpit/src/app/dashboard/page.tsx` | Remove standalone macro panel block + its eyebrow label; pass `week` prop to the unified card |

No backend changes. No new API routes. No migration.
