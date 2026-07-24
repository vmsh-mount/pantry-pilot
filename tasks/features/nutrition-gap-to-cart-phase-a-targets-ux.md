# PRD — Nutrition Gap-to-Cart, Phase A: Personalized Targets UX Layer

**Status:** Ready for implementation
**Date:** 2026-07-24
**Branch:** `feature/nutrition-gap-targets-ux`
**Series:** Gap-to-Cart phase A of 6 — see [`nutrition-gap-to-cart/implementation-plan.md`](../../docs/nutrition-gap-to-cart/implementation-plan.md).
**Companion doc:** [`personalised-nutrition-targets.md`](personalised-nutrition-targets.md) — **authoritative for the target computation.** This PRD does not restate or fork it; it only adds a read surface on top.
**Depends on:** [`personalised-nutrition-targets.md`](personalised-nutrition-targets.md)
**Blocks:** [Phase B3 — Gap detection](nutrition-gap-to-cart-phase-b3-gap-detection.md) (reads the household total), [Phase B4 — Digest & UI](nutrition-gap-to-cart-phase-b4-digest-ui.md) (Screen A)

---

## Problem

The companion PRD computes `personalised_weekly_targets(members, member_count)` — a single household total — and deliberately changes no frontend and adds no endpoint (see its Out of Scope). That's the right call for its own surfaces (`/nutrition/weekly`, `/dashboard`), but two things downstream need more:

1. **B4's Settings screen** wants a per-member breakdown ("Vamsi · 96g protein, Amma · 68g protein…"), not just the household sum — that's the actual UX win of personalizing targets; a bare total doesn't show *why* it changed.
2. **B3's gap diff** needs a stable read path for the household total that can't silently drift from the companion PRD's own calculation.

This PRD is that read layer. It computes nothing new.

## Goals

- One public function, `per_member_targets(member)`, added to the companion PRD's module — the household total becomes its sum, by construction, not a parallel calculation.
- `GET /v1/nutrition/targets` exposing the per-member breakdown.
- Settings UI rendering it.

## Out of Scope

- Any change to the target formulas, fallback hierarchy, or the companion PRD's two existing call sites (`/nutrition/weekly`, `/dashboard`). Zero divergence there.
- Realigning the per-order `NutritionCard.tsx` hardcoded fallback targets — separate surface, not touched by the companion PRD or this one. Candidate follow-up, not a blocker.

---

## Required amendment to the companion PRD

`app/pilot/app/utils/nutrition_targets.py` must export a **public** `per_member_targets(member) -> dict` (daily, single member), with the household function defined as its sum:

```python
def per_member_targets(member: HouseholdMember) -> dict:
    """One member's DAILY targets. Applies the fallback hierarchy."""

def personalised_weekly_targets(members, member_count) -> dict:
    """== sum(per_member_targets(m) for m in members) × 7, with fallbacks."""
```

**Why this matters:** the alternative — this PRD importing the companion module's private `_member_calories` / `_member_protein` / `_member_fiber` / `_member_sodium_ceiling` helpers directly — would make the two docs drift structurally possible instead of structurally impossible. One public function means the endpoint below and the household total are the *same code path*, and the reconciliation test is a formality rather than the only thing holding the contract together.

This amendment has already been applied to `personalised-nutrition-targets.md` — this PRD assumes it's in place, not proposing it as optional.

---

## API Spec

### `GET /v1/nutrition/targets`

**Auth:** household session required.

**Response:**

```json
{
  "success": true,
  "data": {
    "source": "personalized",
    "per_member": [
      {
        "member_id": "…",
        "name": "Vamsi",
        "age_years": 38,
        "daily": { "calories": 2300, "protein_g": 96, "fiber_g": 34, "sodium_mg": 2300 },
        "fallback_used": false
      }
    ],
    "household": {
      "daily":  { "calories": 6100, "protein_g": 232, "fiber_g": 118, "sodium_mg": 8400 },
      "weekly": { "calories": 42700, "protein_g": 1624, "fiber_g": 826, "sodium_mg": 58800 }
    }
  }
}
```

`source` is `"personalized"` when every member has enough biometric data for the Mifflin-St Jeor path; `"role_fallback"` when one or more members fell back to role-based defaults (companion PRD's tier 3). Per-member `fallback_used` lets the UI show which rows are estimated.

`household.weekly` **must** equal `personalised_weekly_targets(members, member_count)` exactly — same function, called once, not recomputed here.

---

## Files to Change

| File | Change |
|---|---|
| `app/pilot/app/utils/nutrition_targets.py` | Add public `per_member_targets()` (companion PRD amendment, see above) |
| `app/pilot/app/api/nutrition.py` | NEW route `GET /targets` |
| `app/cockpit/src/app/settings/targets/page.tsx` | NEW — per-member breakdown screen (mockup: Section A "after") |
| `app/cockpit/src/lib/api.ts` | Add `nutrition.targets()` client method |

## Definition of Done

- [ ] `per_member_targets` is public on the companion module; no private helper is imported outside it.
- [ ] Test: summing `per_member_targets` across a household's members × 7 equals `personalised_weekly_targets`'s output exactly, for the same household — this is the reconciliation test the whole contract rests on.
- [ ] `GET /v1/nutrition/targets` per-member rows sum to the household total in the same response.
- [ ] A member with missing biometrics shows `fallback_used: true` and the UI marks that row as estimated, not blended in silently.
- [ ] No change to `/nutrition/weekly` or `/dashboard` response shape or values beyond what the companion PRD itself specifies.
