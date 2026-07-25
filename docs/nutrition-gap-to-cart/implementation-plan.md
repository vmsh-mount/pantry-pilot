# Nutrition: Personalized Targets → Gap-to-Cart — Implementation Index

**Status:** planning complete — 6 PRDs ready for implementation
**Owner:** —
**Companion doc:** [`ui-mockups.html`](ui-mockups.html) — every screen referenced below, styled to match `NutritionCard.tsx`

---

## The thesis

Every retrospective nutrition app can *detect* a gap. Only PantryPilot can *close* it — because it owns the cart. This feature turns nutrition from a read-only scorecard into a loop:

```
buy → resolve nutrition → measure vs personalized target → recommend SKUs → one-tap into cart → buy
```

Two things unlock it, and both are latent in the schema today:

1. **`household_members` already stores** `age_years, sex, weight_kg, height_cm, activity_level, health_flags` — but targets are computed from `member_count` alone. Personalize them.
2. **`nutrition_cache` is global per-SKU** — every resolution sharpens accuracy for all households. Distill a learned nutrient→food map from it instead of hand-maintaining one.

This document is the index. **Each phase now has its own Ready-for-implementation PRD** in `tasks/features/` — implementation detail lives there, not here. This page tracks the arc, the dependency graph, and the handful of decisions that span every phase.

---

## The six PRDs

| Phase | PRD | Depends on | What it ships |
|---|---|---|---|
| **0** | [`nutrition-gap-to-cart-phase0-schema.md`](../../tasks/features/nutrition-gap-to-cart-phase0-schema.md) | — | Migration: 2 columns on `nutrition_cache`, `nutrient_food_candidate` table, `nutrition_gaps_enabled` flag |
| **A** | [`nutrition-gap-to-cart-phase-a-targets-ux.md`](../../tasks/features/nutrition-gap-to-cart-phase-a-targets-ux.md) | [`personalised-nutrition-targets.md`](../../tasks/features/personalised-nutrition-targets.md) | Public `per_member_targets()`, `GET /v1/nutrition/targets`, per-member Settings UI |
| **B1** | [`nutrition-gap-to-cart-phase-b1-sku-enrichment.md`](../../tasks/features/nutrition-gap-to-cart-phase-b1-sku-enrichment.md) | 0 | SKU → `food_concept` + `notable_nutrients`; the canonical nutrient key vocabulary |
| **B2** | [`nutrition-gap-to-cart-phase-b2-candidate-map.md`](../../tasks/features/nutrition-gap-to-cart-phase-b2-candidate-map.md) | 0, B1 | Nightly learned nutrient→food map, seed floor for cold start |
| **B3** | [`nutrition-gap-to-cart-phase-b3-gap-detection.md`](../../tasks/features/nutrition-gap-to-cart-phase-b3-gap-detection.md) | 0, A, B1, B2 | `GET /v1/nutrition/gaps` — the diff + recommendation endpoint |
| **B4** | [`nutrition-gap-to-cart-phase-b4-digest-ui.md`](../../tasks/features/nutrition-gap-to-cart-phase-b4-digest-ui.md) | A, B3 | Home card, weekly digest, Gap-to-Cart screen, add-to-cart |

```
        ┌── A  (targets UX) ───────────────┐
0 ──────┼── B1 (SKU enrichment) ── B2 ──────┼── B3 ── B4
        └──────────────────────────────────┘
```

**0, A, and B1 have no dependency on each other** and can be built in parallel. B2 needs B1. B3 is the join point — it needs A (targets) and B2 (candidates) both done. B4 is the surface, last.

---

## Companion PRD note

[`personalised-nutrition-targets.md`](../../tasks/features/personalised-nutrition-targets.md) predates this series and is **authoritative for the target computation** (Mifflin-St Jeor, fallback hierarchy, the two existing call sites). Phase A does not fork it — it adds one public function (`per_member_targets`) on top, per that PRD's own amendment note. Don't let a future edit re-introduce a second, drifted target calculation; if the math needs to change, it changes there, and Phase A's reconciliation test will catch drift.

---

## Decisions that span every phase

These came out of two review passes and are enforced as **Definition of Done items in the individual PRDs**, not just prose here — this section is a pointer, not the source of truth.

- **Nutrient vocabulary is pinned once.** Two distinct key spaces exist (`nutrition_cache.nutrients` uses `*_per_100g`; `order_nutrition.nutrient_totals` uses `*_g`). A bare grouping key like `"b12"` matches neither. B1 declares the canonical `NUTRIENT_KEYS` mapping; every other phase imports it. Get this wrong and a gap fails **silently** (empty join, not an error) — see [B1 §Canonical Nutrient Vocabulary](../../tasks/features/nutrition-gap-to-cart-phase-b1-sku-enrichment.md#canonical-nutrient-vocabulary).
- **Coverage guard, not silent false deficiency.** `b12` and `vitamin_d` are emitted only by the LLM resolution path — never OFF, never USDA. "No data" ≠ "no intake." B3 requires ≥60% coverage before flagging a nutrient as short; below that it reports `insufficient_data`, not a deficiency. Health-adjacent, non-negotiable — see [B3 §Coverage Guard](../../tasks/features/nutrition-gap-to-cart-phase-b3-gap-detection.md#coverage-guard).
- **No stored price.** `nutrient_per_rupee` is computed at request time in B3 from a live Swiggy search price. Nothing in Phase 0's schema or B2's nightly job stores a price — there's no persistent price catalog to keep it fresh against.
- **Sodium is a ceiling, not a progress bar.** B4 renders it with a distinct component (`CeilingBar`), never `MacroBar` — a near-full bar on a ceiling means caution, not achievement.
- **Add-to-cart has a defined fallback.** Most digest reads happen with no `awaiting_confirmation` LoopRun open. B4 routes to the pending Flow basket when one exists, else Quick Order — never silently triggers a fresh planning run.
- **Compliance is correctly untouched.** `compute_weekly_compliance` computes no household calorie/protein target (its only protein check is a %-of-calories ratio for `high-protein` diets). Personalizing targets cannot desync it — verified against source, not assumed. No PRD in this series touches it.
- **Attribution caveat.** Nutrition and gaps are household-level, not per-person. Ship labeled as such; per-member intake allocation is an explicit non-goal for this series.
- **Feature-gated.** `households.nutrition_gaps_enabled` (Phase 0) dark-launches B3/B4.

---

## Explicitly out of scope for this series

- Realigning the per-order `NutritionCard.tsx` hardcoded display fallback — a separate surface none of these PRDs touch. Candidate follow-up.
- Per-member intake attribution.
- WhatsApp digest delivery — sequenced after the in-app surface (B4) proves out.

---

## Why this is the USP

MyFitnessPal can detect the same protein gap. It shows you a number. PantryPilot detects it and drops the paneer in your basket, because it owns purchase history, the nutrition resolution chain, catalog search, and the cart. Three phases (B1–B3) exist purely to make that join possible without hand-maintaining a food list or ranking on stale prices; B4 is the three screens where a user actually feels it.
