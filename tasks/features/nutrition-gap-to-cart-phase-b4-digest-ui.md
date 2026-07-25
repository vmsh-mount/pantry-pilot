# PRD — Nutrition Gap-to-Cart, Phase B4: Weekly Digest & Gap-to-Cart UI

**Status:** Ready for implementation
**Date:** 2026-07-24
**Branch:** `feature/nutrition-gap-digest-ui`
**Series:** Gap-to-Cart phase B4 of 5 (final) — see [`nutrition-gap-to-cart/implementation-plan.md`](../../docs/nutrition-gap-to-cart/implementation-plan.md).
**Companion doc:** [`docs/nutrition-gap-to-cart/ui-mockups.html`](../../docs/nutrition-gap-to-cart/ui-mockups.html) — visual reference for every screen below (Sections 0–C), light/dark themed, styled to match `NutritionCard.tsx` exactly.
**Depends on:** [Phase A — Targets UX layer](nutrition-gap-to-cart-phase-a-targets-ux.md), [Phase B3 — Gap detection](nutrition-gap-to-cart-phase-b3-gap-detection.md)
**Blocks:** none (last phase)

---

## Problem

`GET /v1/nutrition/gaps` (B3) has no surface. This PRD is the last mile: a home-screen entry point, a weekly digest, and the Gap-to-Cart recommendation screen with a working add-to-cart action. This is the payoff screen for the whole feature — see the mockup's closing callout: *"it doesn't just name the gap, it puts the fix in the cart."*

## Goals

- One nutrition card on Home, leading with the actionable thing (this week's shortfall), not a static score.
- Weekly digest reusing the existing `MacroBar` component, pointed at personalized targets.
- Sodium rendered as a **ceiling**, visually distinct from a progress bar — not "under limit" reading as "almost there."
- Gap-to-Cart recommendation cards with a **defined add-to-cart routing** that never dead-ends.
- Feature-gated behind `households.nutrition_gaps_enabled` (Phase 0).

## Out of Scope

- Realigning the per-order `NutritionCard.tsx` hardcoded targets — a separate surface the companion targets PRD deliberately does not touch. Not a blocker here; a candidate follow-up.
- WhatsApp digest delivery — sequenced after this in-app surface proves out. The "Send to WhatsApp" button in the mockup is a placeholder for that later work; ship it disabled/hidden if the provider isn't ready.
- Per-member intake attribution — nutrition and gaps remain household-level.

---

## Screen 0 — Home Entry Point

*(mockup: Section 0)*

New card on the home dashboard, below the Flow status card. Leads with the flag, not a number:

- **Eyebrow chip** — `needs attention` (semantic red) when any nutrient has `status: "short"`; otherwise a quiet `on track` state, no chip.
- **One-line summary** — worst gap by relative magnitude, e.g. *"Protein 224g short · B12 missing"* (pull the top 1–2 entries from `/v1/nutrition/gaps`, in the format B3 returns — `short_by`/`unit` for `"short"`, nutrient name only for `"insufficient_data"`).
- **Three entry rows**, each routing to a distinct screen:
  1. **Household targets** → Screen A (Settings)
  2. **This week's report** → Screen B (digest)
  3. **Fix these in my cart** → Screen C (Gap-to-Cart)

Card is hidden entirely if `nutrition_gaps_enabled` is falsy for the household, or if `/v1/nutrition/gaps` has never successfully computed (no `order_nutrition` rows yet).

---

## Screen A — Household Targets (Settings)

*(mockup: Section A "after")*

Per-member breakdown, reading `GET /v1/nutrition/targets` ([Phase A](nutrition-gap-to-cart-phase-a-targets-ux.md)). One row per `HouseholdMember`: avatar/role icon, name, age, weight/activity summary, daily calories, daily protein. Footer row sums to the household daily total. A chip surfaces any `health_flags`-driven adjustment (e.g. "Amma's sodium ceiling applied").

If a member has no biometric data, their row shows the role-fallback value with a small "estimated" label — never silently blend it in as if it were personalized.

---

## Screen B — Weekly Digest

*(mockup: Section B)*

Reuses the existing `MacroBar` component (`NutritionCard.tsx`), now driven by `personalised_weekly_targets` instead of a hardcoded constant:

- **Calories hero** — actual / target, same visual treatment as the per-order card.
- **Protein, Fiber** — standard `MacroBar` fill (0–100%, capped).
- **Sodium — ceiling treatment, not a `MacroBar`.** This is a deliberate visual departure and must not reuse the progress-fill component:
  - Amber track (not the brand green/blue macro colors) with a hard limit marker at the right edge.
  - Label reads `under limit ✓` (semantic green) or `approaching limit` (amber) or `over limit` (red) — never a bare percentage, since "92% of a ceiling" reads as almost-there when it should read as caution.
  - See mockup CSS (`.ceiling`, `.cei-track`, `.cei-fill`) for the exact treatment.
- **No Fat/Carb bars.** The targets PRD computes only calories, protein, fiber, and the sodium ceiling — there is no fat/carb target to bar against. Do not render one.
- **Flagged section** — one row per gap from `/v1/nutrition/gaps`:
  - `status: "short"` → red `short` chip + `"{Nutrient} — {short_by}{unit} under target"`
  - `status: "insufficient_data"` → neutral chip + `"{Nutrient} — not enough data to assess"` (never phrased as a deficiency)
- **Single CTA** — "Fix these in my cart" → Screen C. The digest never dead-ends on a number.

---

## Screen C — Gap-to-Cart

*(mockup: Section C)*

For each gap (grouped by nutrient), render `GET /v1/nutrition/gaps`'s `recommendations` as cards:

- Thumbnail (image or emoji fallback, matching `ItemSearchDropdown`'s pattern), item name, brand.
- **Confidence badge** — reuse `NutritionCard.tsx`'s existing `ConfidenceBadge` component verbatim (same colors/labels: Verified / Label / ~Database / ~AI est.). Do not invent a new badge style.
- **`delivers` line** — `+{Xg} {nutrient}`, plus `· best per ₹` on the top-ranked item for that gap.
- **`reordered {N}×`** when `repurchase_rate` / `order_frequency` indicate a real signal — this is the learned-map differentiator, surface it.
- Price + **Add** button per card.

### Add-to-cart routing (must be defined — this is the part that broke in review)

The primary CTA is "Add all to cart" / per-card "Add". Routing:

1. **If an `awaiting_confirmation` LoopRun exists** for the household → `POST /v1/basket/add-item` (adds to the pending Flow basket).
2. **Otherwise** (the common case — a digest is usually read outside the confirmation window) → add to a **Quick Order** basket via the existing quick-add path, and route the user there.
3. **Do not** silently trigger a new Flow planning run from this button. If no basket exists and the user wants a full replan, that's a separate, explicit action elsewhere in the app — this button must not surprise the user with an unexpected planning run or its cost.

Button copy is **"Add to cart"**, not "Add to Flow basket" — the copy must not imply a routing guarantee it can't keep.

### One-tap-closes-everything framing — avoid over-promising

A single pack (e.g. 1kg toor dal) can numerically close an entire weekly gap in the `nutrient_per_100g` math, but that's a week-plus of dal, not one sitting, and nutrition here is household-level, not per-person. Copy should read `"≈220g over the week"` rather than implying one purchase = one meal's fix. See mockup Section C for the exact phrasing.

---

## API Spec

No new backend endpoints — this PRD is pure frontend, consuming:
- `GET /v1/nutrition/targets` (Phase A)
- `GET /v1/nutrition/gaps` (B3)
- `POST /v1/basket/add-item` (existing)
- Quick Order add (existing)
- `PATCH /v1/settings` — extended to accept `nutrition_gaps_enabled` (toggle, for dark-launch / opt-out)

## Files to Change

| File | Change |
|---|---|
| `app/cockpit/src/components/nutrition/NutritionGapsCard.tsx` | NEW — Home entry card (Screen 0) |
| `app/cockpit/src/app/nutrition/weekly/page.tsx` | NEW — digest (Screen B), reuses `MacroBar` |
| `app/cockpit/src/components/nutrition/CeilingBar.tsx` | NEW — sodium's distinct ceiling component |
| `app/cockpit/src/app/nutrition/gaps/page.tsx` | NEW — Gap-to-Cart (Screen C) |
| `app/cockpit/src/app/settings/targets/page.tsx` | NEW — per-member breakdown (Screen A) |
| `app/cockpit/src/lib/api.ts` | Add `nutrition.targets()`, `nutrition.gaps()` client methods |

## Definition of Done

- [ ] Home card hidden when `nutrition_gaps_enabled` is falsy or no `order_nutrition` data exists yet.
- [ ] Sodium renders via `CeilingBar`, never `MacroBar` — visually distinguishable from protein/fiber at a glance.
- [ ] No Fat/Carb bar rendered anywhere in the digest.
- [ ] `insufficient_data` nutrients render as "not enough data," never as a flagged shortfall.
- [ ] Add-to-cart routes correctly in both states (pending Flow basket / no pending basket) — covered by a Playwright E2E extending the existing `qa/` suite.
- [ ] Confidence badges on Gap-to-Cart cards are the literal `ConfidenceBadge` component from `NutritionCard.tsx`, not a re-implementation.
- [ ] Full loop verified end-to-end: gap shown → item added → next order placed → next week's `order_nutrition` reflects the closed gap.
