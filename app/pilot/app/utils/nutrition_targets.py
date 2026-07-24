"""
Personalised nutrition targets.

Replaces the flat headcount multiplier (member_count × 2000 kcal etc.) with a
per-member physiological calculation summed across the household:

  - Calories : Mifflin-St Jeor BMR × activity multiplier for adults (18+);
               flat age-band defaults for members under 18.
  - Protein  : g/kg bodyweight, higher for elderly and active adults.
  - Fiber    : age/sex-adjusted daily grams.
  - Sodium   : a daily *ceiling* (upper limit), tightened by health_flags.

Members with missing biometric data fall back through a role-based hierarchy
so the household always gets a sane target. Values returned are WEEKLY totals
(daily × 7) to match the existing API contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.db import HouseholdMember


# ── Activity multipliers (map HouseholdMember.activity_level) ──────────────────
_ACTIVITY = {
    "sedentary":         1.20,
    "lightly_active":    1.375,
    "moderately_active": 1.55,
    "very_active":       1.725,
    "extra_active":      1.90,
}
_ACTIVITY_DEFAULT = 1.375  # lightly active

# ── Role-based flat defaults (daily): (calories, protein_g, fiber_g, sodium_mg) ─
_ROLE_DEFAULTS = {
    "adult":   (2000, 50, 25, 2300),
    "elderly": (1800, 60, 21, 2300),
    "child":   (1500, 25, 19, 2300),
    "infant":  (1000, 13, 14, 1500),
}
_ROLE_DEFAULT = _ROLE_DEFAULTS["adult"]


def _num(value) -> float | None:
    """Coerce a Numeric/None DB value to float, or None if absent."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _child_calories(age: int) -> int:
    if age < 4:
        return 1000
    if age <= 8:
        return 1400
    if age <= 13:
        return 1800
    return 2000  # 14–17


def _member_calories(m: "HouseholdMember") -> int:
    age    = m.age_years
    weight = _num(m.weight_kg)
    height = _num(m.height_cm)
    sex    = (m.sex or "").lower()

    # Under-18: age-band defaults (Mifflin-St Jeor does not model growth needs).
    if age is not None and age < 18:
        return _child_calories(age)

    # Adult with full biometrics: Mifflin-St Jeor × activity.
    if age is not None and weight is not None and height is not None:
        if sex == "male":
            bmr = 10 * weight + 6.25 * height - 5 * age + 5
        elif sex == "female":
            bmr = 10 * weight + 6.25 * height - 5 * age - 161
        else:
            male   = 10 * weight + 6.25 * height - 5 * age + 5
            female = 10 * weight + 6.25 * height - 5 * age - 161
            bmr = (male + female) / 2
        mult = _ACTIVITY.get((m.activity_level or "").lower(), _ACTIVITY_DEFAULT)
        return int(round(bmr * mult / 50.0)) * 50

    # Partial data: age known but biometrics missing → elderly vs adult default.
    if age is not None:
        role = "elderly" if age >= 65 else "adult"
        return _ROLE_DEFAULTS[role][0]

    # No data: role-based default.
    return _role_defaults(m)[0]


def _member_protein(m: "HouseholdMember") -> float:
    age    = m.age_years
    weight = _num(m.weight_kg)

    if age is not None and age < 10:
        return 20.0
    if age is not None and age < 18:
        if weight is not None:
            return round(1.0 * weight, 1)
        return 40.0

    # Adult / elderly
    if weight is not None and age is not None:
        if age >= 65:
            g_per_kg = 1.2
        else:
            activity = (m.activity_level or "").lower()
            if activity in ("very_active", "extra_active"):
                g_per_kg = 1.6
            elif activity == "moderately_active":
                g_per_kg = 1.2
            else:
                g_per_kg = 0.8
        return round(g_per_kg * weight, 1)

    # Missing weight → flat fallback by age/role.
    if age is not None:
        return 60.0 if age >= 65 else 50.0
    return float(_role_defaults(m)[1])


def _member_fiber(m: "HouseholdMember") -> float:
    age = m.age_years
    sex = (m.sex or "").lower()

    if age is not None:
        if age >= 65:
            return 21.0
        if age >= 18:
            return 38.0 if sex == "male" else 25.0
        if age >= 14:
            return 26.0 if sex == "male" else 20.0
        if age >= 9:
            return 25.0 if sex == "male" else 20.0
        if age >= 4:
            return 19.0
        return 14.0

    return float(_role_defaults(m)[2])


def _member_sodium_ceiling(m: "HouseholdMember") -> int:
    flags = {f.lower() for f in (m.health_flags or [])}
    if "hypertension" in flags:
        return 1500  # stricter wins
    if "kidney_disease" in flags:
        return 2000
    return int(_role_defaults(m)[3])


def _role_defaults(m: "HouseholdMember") -> tuple:
    role = (m.role or "adult").lower()
    return _ROLE_DEFAULTS.get(role, _ROLE_DEFAULT)


def per_member_targets(member: "HouseholdMember") -> dict:
    """
    One member's DAILY targets. Applies the fallback hierarchy for missing
    biometrics (full data -> Mifflin-St Jeor; partial data -> age-band /
    role-by-age defaults; no data -> role-based flat defaults).

    Public by design: this is the shared code path between the household
    total below and any consumer needing a per-member breakdown (e.g.
    GET /v1/nutrition/targets) — never reach into the private _member_*
    helpers directly, or the two call sites can drift.

    Returns: {"calories": int, "protein_g": float, "fiber_g": float, "sodium_mg": int}
    """
    return {
        "calories":  _member_calories(member),
        "protein_g": _member_protein(member),
        "fiber_g":   _member_fiber(member),
        "sodium_mg": _member_sodium_ceiling(member),
    }


def _unmapped_member_targets() -> dict:
    """Daily targets for a member_count slot with no HouseholdMember row (adult default)."""
    c, p, f, s = _ROLE_DEFAULT
    return {"calories": c, "protein_g": float(p), "fiber_g": float(f), "sodium_mg": s}


def personalised_weekly_targets(
    members: list["HouseholdMember"],
    member_count: int,
) -> dict:
    """
    Compute weekly nutrition targets for the household.

    Defined as the sum of per_member_targets() across members, x 7 — the
    household total is never computed independently of the per-member path.
    If there are fewer HouseholdMember rows than member_count, each unmapped
    member is scored via _unmapped_member_targets() (adult default) so the
    household total reflects everyone.

    Returns weekly totals:
        {"calories": int, "protein_g": float, "fiber_g": float, "sodium_mg": int}
    """
    daily_cal = 0.0
    daily_pro = 0.0
    daily_fib = 0.0
    daily_sod = 0.0

    for m in members:
        daily = per_member_targets(m)
        daily_cal += daily["calories"]
        daily_pro += daily["protein_g"]
        daily_fib += daily["fiber_g"]
        daily_sod += daily["sodium_mg"]

    # Unmapped members (member_count exceeds rows) → adult defaults.
    unmapped = max(0, (member_count or 0) - len(members))
    if unmapped:
        daily = _unmapped_member_targets()
        daily_cal += daily["calories"] * unmapped
        daily_pro += daily["protein_g"] * unmapped
        daily_fib += daily["fiber_g"] * unmapped
        daily_sod += daily["sodium_mg"] * unmapped

    return {
        "calories":  int(round(daily_cal * 7)),
        "protein_g": round(daily_pro * 7, 1),
        "fiber_g":   round(daily_fib * 7, 1),
        "sodium_mg": int(round(daily_sod * 7)),
    }
