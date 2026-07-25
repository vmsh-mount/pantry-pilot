# PRD — Personalised Nutrition Targets

**Status:** Ready for implementation  
**Date:** 2026-07-24  
**Branch:** `feature/personalised-nutrition-targets`

---

## Problem

Every nutrition target is computed from a headcount:

```python
# api/nutrition.py
def _icmr_weekly_targets(member_count: int) -> dict:
    return {k: v * 7 for k, v in {"calories": member_count * 2000, ...}.items()}

# api/dashboard.py (inline duplicate)
icmr = {"calories": _d[0] * member_count * 7, ...}
```

A family of 4 — active 38M (75 kg), sedentary 35F (60 kg), 8-year-old, 70-year-old grandmother — gets:

| Nutrient | Current target | Correct target | Error |
|---|---|---|---|
| Calories/day | 8,000 | ~6,100 | +31% overstated |
| Protein/day | 200 g | ~232 g | −15% understated |

The dashboard tells this family they are under-eating when they are fine, and that protein is fine when they are short. Every downstream flag, trend, and compliance alert inherits this error.

`HouseholdMember` already stores exactly what's needed: `age_years`, `sex`, `weight_kg`, `height_cm`, `activity_level`, `health_flags`. The fix is one function that replaces the headcount multiplier.

---

## Goals

- Replace flat headcount targets with per-member physiological calculations
- Single shared function consumed by both callers (`nutrition.py` and `dashboard.py`)
- Graceful fallback for members with missing biometric data
- No schema changes, no migration

---

## Out of Scope

- UI for entering member biometrics (already exists in onboarding)
- Storing computed targets in DB (recompute on each request — fast, always current)
- Macro split targets (carbs/fat) — calories and the four tracked nutrients are enough

---

## Target Calculation — Per Member

### Calories — Mifflin-St Jeor BMR × Activity Multiplier

```
Male:   BMR = 10 × weight_kg + 6.25 × height_cm − 5 × age_years + 5
Female: BMR = 10 × weight_kg + 6.25 × height_cm − 5 × age_years − 161
Other:  BMR = average of male and female formulas
```

Activity multiplier (maps `HouseholdMember.activity_level`):

| `activity_level` value | Multiplier |
|---|---|
| `sedentary` | 1.20 |
| `lightly_active` | 1.375 |
| `moderately_active` | 1.55 |
| `very_active` | 1.725 |
| `extra_active` | 1.90 |
| None / unknown | 1.375 (lightly active default) |

**Daily calories = BMR × multiplier**, rounded to nearest 50.

Mifflin-St Jeor applies only to members **aged 18 and over**. For any member under 18, it is unreliable (it does not account for growth energy needs) — use flat age-band defaults instead:

| Age | Daily calories |
|---|---|
| < 4 (infant) | 1,000 |
| 4–8 | 1,400 |
| 9–13 | 1,800 |
| 14–17 | 2,000 |

So the branch is: `age_years >= 18` → Mifflin-St Jeor + activity multiplier; `age_years < 18` → age-band lookup above. This makes the calorie path and the protein/fiber under-18 rows below consistent — no member is scored by both methods.

### Protein — g/kg Bodyweight

| Profile | g/kg/day |
|---|---|
| Adult (18–64), sedentary / lightly active | 0.8 |
| Adult (18–64), moderately active | 1.2 |
| Adult (18–64), very active / extra active | 1.6 |
| Elderly (≥ 65) | 1.2 (anabolic resistance requires more per kg) |
| Child / teen (10–17) | 1.0 |
| Child < 10 | flat 20 g/day |

If `weight_kg` is missing, fall back to flat defaults (adult 50 g, elderly 60 g, child 20 g).

### Fiber

| Age / sex | g/day |
|---|---|
| Adult male | 38 |
| Adult female | 25 |
| Elderly (≥ 65), any sex | 21 |
| Teen (14–17) | 26 (M) / 20 (F) |
| Child 9–13 | 25 (M) / 20 (F) |
| Child 4–8 | 19 |
| Child < 4 | 14 |

If `sex` is missing, use 25 g.

### Sodium Ceiling

| Condition | mg/day |
|---|---|
| Default | 2,300 |
| `hypertension` in `health_flags` | 1,500 |
| `kidney_disease` in `health_flags` | 2,000 |
| Both flags | 1,500 (stricter wins) |

Sodium is a ceiling (upper limit), not a target. The compliance alert fires when the household exceeds it.

---

## Fallback Hierarchy

Not all members have complete biometric data. Apply in order:

1. **Full data** (`weight_kg`, `height_cm`, `age_years`, `sex` all present): use Mifflin-St Jeor + activity multiplier.
2. **Partial data** (`age_years` present, biometrics missing): use age-band defaults for calories, flat protein/fiber by age/sex.
3. **No data** (member with no fields set): use role-based flat defaults:

| `role` | Calories | Protein | Fiber | Sodium |
|---|---|---|---|---|
| `adult` / None | 2,000 | 50 g | 25 g | 2,300 mg |
| `elderly` | 1,800 | 60 g | 21 g | 2,300 mg |
| `child` | 1,500 | 25 g | 19 g | 2,300 mg |
| `infant` | 1,000 | 13 g | 14 g | 1,500 mg |

If the household has `member_count` but zero `HouseholdMember` rows (members not yet set up), treat each unmapped member as `role=None` → adult default.

---

## Implementation

### New shared utility: `app/utils/nutrition_targets.py`

```python
def per_member_targets(member: HouseholdMember) -> dict:
    """
    One member's DAILY targets: {"calories", "protein_g", "fiber_g", "sodium_mg"}.
    Applies the fallback hierarchy for missing biometrics.
    """

def personalised_weekly_targets(members: list[HouseholdMember], member_count: int) -> dict:
    """
    Compute weekly nutrition targets for the household.
    Defined as the sum of per_member_targets() across members, × 7.
    Falls back to role/headcount defaults for members with missing biometrics.
    """
```

**Two public functions**, and the household total is *by construction* the sum of the per-member one — so any consumer that needs the breakdown (see `docs/nutrition-gap-to-cart/implementation-plan.md`, which adds `GET /v1/nutrition/targets`) shares this exact code path instead of reaching into private helpers and drifting.

`personalised_weekly_targets` returns the same shape as `_icmr_weekly_targets`:
```python
{"calories": int, "protein_g": float, "fiber_g": float, "sodium_mg": int}
```
Values are **weekly totals** (daily × 7), matching the existing API contract. `per_member_targets` returns the same keys but **daily**, for one member.

Internal helpers (private, same file) — called only by `per_member_targets`:
- `_member_calories(m)` → daily kcal
- `_member_protein(m)` → daily g
- `_member_fiber(m)` → daily g
- `_member_sodium_ceiling(m)` → daily mg

### Changes to callers

**`api/nutrition.py`** (`GET /v1/nutrition/weekly`):
- Load `HouseholdMember` rows for `household_id` alongside `Household`
- Replace `_icmr_weekly_targets(hh.member_count)` with `personalised_weekly_targets(members, hh.member_count)`
- Remove `_icmr_weekly_targets` function

**`api/dashboard.py`** (`GET /v1/dashboard`):
- Load `HouseholdMember` rows (already loads `Household`)
- Replace the inline ICMR block (lines 135–144) with `personalised_weekly_targets(members, hh.member_count)`
- Import from `app.utils.nutrition_targets`

### No other changes

`GET /v1/nutrition/order/{order_id}` — not affected (returns actuals, no targets).  
`GET /v1/nutrition/compliance` — **not affected.** Verified against `compute_weekly_compliance` ([tasks/nutrition.py:163](app/pilot/app/tasks/nutrition.py)): it does not compute household calorie/protein targets at all. Its flags are per-item, per-100g threshold checks (sugar > 20/100g, sodium > 600/100g, saturated fat > 5/100g) plus a protein-as-%-of-calories check. There is no `member_count` multiplier and no aggregate calorie/protein target in that task, so personalising household targets cannot desync it. No change needed.  
`PATCH /v1/nutrition/goals` — user-set overrides remain untouched; they take priority over computed targets.

---

## API Contract — No Breaking Change

`weekly_targets` shape in `GET /v1/nutrition/weekly` response stays identical:

```json
"weekly_targets": {
  "calories":  42700,
  "protein_g": 1624,
  "fiber_g":   175,
  "sodium_mg": 10500
}
```

Values change (they become accurate), shape does not. Frontend NutritionCard renders whatever the API returns — no frontend changes needed.

---

## Files to Change

| File | Change |
|---|---|
| `app/pilot/app/utils/nutrition_targets.py` | NEW — public `per_member_targets()` + `personalised_weekly_targets()` (the sum of it) + private helpers |
| `app/pilot/app/api/nutrition.py` | Load members, call `personalised_weekly_targets`, remove `_icmr_weekly_targets` |
| `app/pilot/app/api/dashboard.py` | Load members, replace inline ICMR block with `personalised_weekly_targets` |

No migration. No schema changes. No frontend changes.
