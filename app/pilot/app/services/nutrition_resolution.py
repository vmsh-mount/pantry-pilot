"""
Nutrition resolution chain: Open Food Facts → USDA FoodData Central → Claude Haiku.

Resolution is per-SKU and globally cached:
  1. Redis (30-day TTL hot cache)
  2. DB nutrition_cache (persistent cold cache)
  3. OFF API → HIGH or MEDIUM confidence
  4. USDA API → MEDIUM confidence
  5. Claude Haiku → ESTIMATE confidence

A resolved entry is written to both caches so subsequent lookups are instant.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import get_settings
from app.models.db import NutritionCache
from app.redis import get_redis
from app.utils.logging import get_logger

logger = get_logger(__name__)

_REDIS_TTL = 60 * 60 * 24 * 30  # 30 days

# ── Gap-to-Cart: canonical nutrient grouping-key vocabulary ─────────────────────
# Used across nutrition_cache, order_nutrition, and the Gap-to-Cart feature
# (nutrient_food_candidate, /v1/nutrition/gaps). Every consumer imports this —
# do not restate the mapping elsewhere (see Phase B1 PRD).
#
#   density: nutrition_cache.nutrients key (per-100g) — B2 reads this space.
#   total:   order_nutrition.nutrient_totals key (absolute) — B3 reads this space.
#            Derived mechanically in compute_item_totals() via "_per_100g" -> "_g".
NUTRIENT_KEYS: dict[str, dict[str, str]] = {
    "protein":        {"density": "protein_per_100g",        "total": "protein_g"},
    "fiber":          {"density": "fiber_per_100g",           "total": "fiber_g"},
    "sodium":         {"density": "sodium_mg_per_100g",       "total": "sodium_mg"},
    "iron":           {"density": "iron_per_100g",            "total": "iron_g"},
    "calcium":        {"density": "calcium_per_100g",         "total": "calcium_g"},
    "b12":            {"density": "vitamin_b12_per_100g",     "total": "vitamin_b12_g"},
    "vitamin_d":      {"density": "vitamin_d_per_100g",       "total": "vitamin_d_g"},
    "vitamin_c":      {"density": "vitamin_c_per_100g",       "total": "vitamin_c_g"},
    "potassium":      {"density": "potassium_per_100g",       "total": "potassium_g"},
    "sugar":          {"density": "sugar_per_100g",           "total": "sugar_g"},
    "saturated_fat":  {"density": "saturated_fat_per_100g",   "total": "saturated_fat_g"},
}

# notable_nutrients is "this food is a meaningful SOURCE of X" — used by B2/B3 to
# recommend foods that close a gap. sodium/sugar/saturated_fat are ceilings/limits,
# not benefits: a food being "high" in them is never a reason to recommend it for a
# gap, so they're excluded from candidacy regardless of source.
_ALLOWED_NOTABLE_KEYS = frozenset(NUTRIENT_KEYS) - {"sodium", "sugar", "saturated_fat"}

# Mechanical "good source" floors (per 100g) — used for OFF/USDA rows (no LLM call)
# and as a sanity check on the LLM's own notable_nutrients claim.
_NOTABLE_FLOORS: dict[str, float] = {
    "protein":   5.0,    # g/100g
    "fiber":     3.0,    # g/100g
    "iron":      2.0,    # mg/100g
    "calcium":   100.0,  # mg/100g
    "potassium": 200.0,  # mg/100g
    "vitamin_c": 10.0,   # mg/100g
    "b12":       0.5,    # mcg/100g
    "vitamin_d": 1.0,    # mcg/100g
}

# Descriptor/marketing tokens stripped before taking the core food noun.
_CONCEPT_STOPWORDS = frozenset({
    "pasteurised", "pasteurized", "toned", "full", "cream", "skimmed", "fresh",
    "organic", "natural", "premium", "pure", "packaged", "homogenised",
    "homogenized", "standardised", "standardized", "farm", "daily", "health",
    "gold", "classic", "original", "unsalted", "salted", "extra", "virgin",
    "cold", "pressed", "whole", "homestyle", "traditional", "deluxe", "super",
    "mega", "value", "pack", "combo", "family", "special", "select", "choice",
    "a2", "raw", "boiled", "and", "with", "the", "of", "in",
})

# Approximate density g/ml for common liquid categories
_LIQUID_DENSITY: dict[str, float] = {
    "milk": 1.03, "juice": 1.04, "oil": 0.92, "water": 1.0,
    "yogurt": 1.03, "curd": 1.03, "butter": 0.91, "ghee": 0.91,
}

# Approximate unit weights in grams for countable items
_UNIT_WEIGHTS_G: dict[str, float] = {
    "egg": 55.0, "banana": 120.0, "apple": 180.0, "orange": 150.0,
    "potato": 150.0, "onion": 110.0, "tomato": 100.0, "lemon": 60.0,
    "garlic clove": 5.0, "green chilli": 10.0,
}


# ── Quantity normalization ────────────────────────────────────────────────────

def _parse_quantity_g(qty_desc: str, item_name: str) -> float | None:
    """Convert a Swiggy quantity description to grams. Returns None if unresolvable."""
    if not qty_desc:
        return None
    s = qty_desc.lower().strip()

    # Weight: "1 kg", "500 g", "500gm", "250 grams"
    m = re.search(r"([\d.]+)\s*(kg|kilogram)", s)
    if m:
        return float(m.group(1)) * 1000

    m = re.search(r"([\d.]+)\s*(g|gm|gram|grams)", s)
    if m:
        return float(m.group(1))

    # Volume: "1 l", "500 ml"
    m = re.search(r"([\d.]+)\s*(l|litre|liter|liters)", s)
    if m:
        ml = float(m.group(1)) * 1000
        density = _liquid_density_for(item_name)
        return ml * density

    m = re.search(r"([\d.]+)\s*ml", s)
    if m:
        ml = float(m.group(1))
        density = _liquid_density_for(item_name)
        return ml * density

    # Count × known unit weight
    for unit, weight in _UNIT_WEIGHTS_G.items():
        m = re.search(rf"(\d+)\s*{unit}", s)
        if m:
            return float(m.group(1)) * weight

    return None


def _liquid_density_for(item_name: str) -> float:
    name_lower = item_name.lower()
    for keyword, density in _LIQUID_DENSITY.items():
        if keyword in name_lower:
            return density
    return 1.0


# ── Gap-to-Cart: mechanical food_concept + notable_nutrients (B1) ───────────────

def _mechanical_food_concept(name: str | None, brand: str | None) -> str | None:
    """
    Cheap, non-LLM canonical-food extraction: strip brand, strip descriptor
    stopwords, strip quantity tokens, take the last remaining token, singularize.
    "Nandini Pasteurised Toned Milk" -> "milk". "Toor Dal" -> "dal".
    Not a judgment call — mechanical only. Returns None if nothing recognizable
    remains (left null in the schema; B2 skips concept-less rows).
    """
    if not name:
        return None

    s = name.lower()
    if brand:
        s = re.sub(re.escape(brand.lower()), " ", s)
    # Strip parenthetical content, e.g. "(Nut & Bean Feed)" — descriptive noise
    # about the product, not the food itself.
    s = re.sub(r"\([^)]*\)", " ", s)
    # Strip quantity/size tokens, same pattern as _clean_item_name.
    s = re.sub(r"\b\d+\.?\d*\s*(g|gm|kg|ml|l|oz|lb|pieces?|pcs?)\b", " ", s)
    # Strip punctuation.
    s = re.sub(r"[^a-z\s]", " ", s)

    tokens = [t for t in s.split() if t and t not in _CONCEPT_STOPWORDS]
    if not tokens:
        return None

    concept = tokens[-1]
    # Crude singularize.
    if concept.endswith("ies") and len(concept) > 4:
        concept = concept[:-3] + "y"
    elif concept.endswith("es") and len(concept) > 4 and not concept.endswith("ses"):
        concept = concept[:-2]
    elif concept.endswith("s") and len(concept) > 3 and not concept.endswith("ss"):
        concept = concept[:-1]

    return concept or None


def _density_value(resolved: dict, nutrient_key: str) -> float | None:
    """Read a nutrient's per-100g density from `resolved`, whichever space it's in
    (first-class column like protein_per_100g, or nested under resolved['nutrients'])."""
    field = NUTRIENT_KEYS[nutrient_key]["density"]
    val = resolved.get(field)
    if val is None:
        val = (resolved.get("nutrients") or {}).get(field)
    return val


def _mechanical_notable_nutrients(resolved: dict) -> list[str]:
    """Floor-based candidacy from whatever per-100g values are already populated
    in `resolved` (any source). Never asserts a nutrient the source didn't
    provide a non-null value for — floors are checked against real values only."""
    out = []
    for key, floor in _NOTABLE_FLOORS.items():
        val = _density_value(resolved, key)
        if val is not None and val >= floor:
            out.append(key)
    return out


# ── Redis cache helpers ───────────────────────────────────────────────────────

def _redis_key(sku_id: str) -> str:
    return f"nutrition:{sku_id}"


async def _redis_get(sku_id: str) -> dict | None:
    redis = await get_redis()
    raw = await redis.get(_redis_key(sku_id))
    return json.loads(raw) if raw else None


async def _redis_set(sku_id: str, data: dict) -> None:
    redis = await get_redis()
    await redis.set(_redis_key(sku_id), json.dumps(data), ex=_REDIS_TTL)


def _row_to_dict(row: NutritionCache) -> dict:
    return {
        "sku_id": row.sku_id,
        "source": row.source,
        "confidence": row.confidence,
        "quantity_g": row.quantity_g,
        "quantity_unresolvable": row.quantity_unresolvable,
        "serving_size_g": row.serving_size_g,
        "calories_per_100g": row.calories_per_100g,
        "protein_per_100g": row.protein_per_100g,
        "total_carbs_per_100g": row.total_carbs_per_100g,
        "fat_per_100g": row.fat_per_100g,
        "fiber_per_100g": row.fiber_per_100g,
        "sodium_mg_per_100g": row.sodium_mg_per_100g,
        "nutrients": row.nutrients or {},
        "matched_name": row.matched_name,
        "nutriscore_grade": row.nutriscore_grade,
        "food_concept": row.food_concept,
        "notable_nutrients": row.notable_nutrients or [],
    }


# ── Open Food Facts ───────────────────────────────────────────────────────────

def _clean_item_name(item_name: str) -> str:
    """Strip Swiggy marketing noise for a cleaner OFF search query."""
    # Remove parenthetical content: "Lay's (India's Magic Masala)" → "Lay's"
    s = re.sub(r"\([^)]*\)", "", item_name)
    # Remove weight/volume tokens like "20g", "500ml", "1kg"
    s = re.sub(r"\b\d+\s*(g|gm|kg|ml|l|oz|lb)\b", "", s, flags=re.IGNORECASE)
    # Collapse whitespace
    return " ".join(s.split())


async def _off_search_query(query: str) -> list:
    params = {
        "search_terms": query,
        "json": "1",
        "page_size": "5",
        "fields": "product_name,brands,nutriments,nutriscore_grade,serving_size",
    }
    async with httpx.AsyncClient(timeout=4.0) as client:
        r = await client.get("https://world.openfoodfacts.org/cgi/search.pl", params=params)
    if r.status_code != 200:
        return []
    return r.json().get("products", [])


async def _search_off(item_name: str, brand: str | None) -> dict | None:
    clean_name = _clean_item_name(item_name)
    # Build two queries: full (brand + cleaned name) and short (brand + first 3 words)
    short_words = " ".join(clean_name.split()[:3])
    queries = []
    if brand:
        queries.append(f"{brand} {clean_name}")
        queries.append(f"{brand} {short_words}")
    else:
        queries.append(clean_name)
        queries.append(short_words)

    products: list = []
    try:
        for q in queries:
            products = await _off_search_query(q)
            if products:
                break
    except Exception as e:
        logger.warning("off_search_failed", error=str(e))
        return None

    if not products:
        return None

    best: dict | None = None
    best_score = 0

    # Tokenise the full Swiggy name so we can check if OFF tokens appear in it
    swiggy_tokens = set(item_name.lower().split())
    brand_lower = (brand or "").lower()

    for p in products:
        score = 0
        p_brand = (p.get("brands") or "").lower()
        p_name = (p.get("product_name") or "").lower()
        n = p.get("nutriments") or {}

        if "energy-kcal_100g" not in n:
            continue

        if brand_lower and brand_lower in p_brand:
            score += 2

        # Score by how many OFF name tokens appear in the (longer) Swiggy name
        p_tokens = set(p_name.split())
        if p_tokens:
            off_in_swiggy = len(p_tokens & swiggy_tokens) / len(p_tokens)
            if off_in_swiggy >= 0.6:
                score += 3
            elif off_in_swiggy >= 0.3:
                score += 1

        if score > best_score:
            best_score = score
            best = p

    if not best or best_score < 2:
        return None

    confidence = "high" if best_score >= 4 else "medium"
    n = best.get("nutriments", {})

    # Parse serving size from string like "30g" or "1 serving (30g)"
    serving_size_g: float | None = None
    ss_raw = best.get("serving_size") or ""
    m = re.search(r"([\d.]+)\s*g", ss_raw)
    if m:
        serving_size_g = float(m.group(1))

    return {
        "source": "off",
        "confidence": confidence,
        "serving_size_g": serving_size_g,
        "calories_per_100g": n.get("energy-kcal_100g"),
        "protein_per_100g": n.get("proteins_100g"),
        "total_carbs_per_100g": n.get("carbohydrates_100g"),
        "fat_per_100g": n.get("fat_100g"),
        "fiber_per_100g": n.get("fiber_100g"),
        "sodium_mg_per_100g": (n.get("sodium_100g") or 0) * 1000,  # OFF stores sodium in g
        "nutrients": {
            "sugar_per_100g": n.get("sugars_100g"),
            "saturated_fat_per_100g": n.get("saturated-fat_100g"),
            "salt_per_100g": n.get("salt_100g"),
        },
        "nutriscore_grade": best.get("nutriscore_grade"),
        "matched_name": best.get("product_name"),
        "off_product_id": best.get("_id") or best.get("code"),
        "raw_data": {"source": "off", "product": {k: best.get(k) for k in ["product_name", "brands", "nutriscore_grade"]}},
    }


# ── USDA FoodData Central ─────────────────────────────────────────────────────

async def _search_usda(item_name: str) -> dict | None:
    api_key = get_settings().usda_api_key
    if not api_key:
        return None
    params = {
        "query": item_name,
        "dataType": "Foundation,SR Legacy",
        "pageSize": "5",
        "api_key": api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get("https://api.nal.usda.gov/fdc/v1/foods/search", params=params)
        if r.status_code != 200:
            return None
        foods = r.json().get("foods", [])
    except Exception as e:
        logger.warning("usda_search_failed", error=str(e))
        return None

    if not foods:
        return None

    food = foods[0]
    nutrients_raw = {n["nutrientName"]: n.get("value") for n in food.get("foodNutrients", [])}

    def _n(key: str) -> float | None:
        return nutrients_raw.get(key)

    return {
        "source": "usda",
        "confidence": "medium",
        "serving_size_g": None,
        "calories_per_100g": _n("Energy"),
        "protein_per_100g": _n("Protein"),
        "total_carbs_per_100g": _n("Carbohydrate, by difference"),
        "fat_per_100g": _n("Total lipid (fat)"),
        "fiber_per_100g": _n("Fiber, total dietary"),
        "sodium_mg_per_100g": _n("Sodium, Na"),
        "nutrients": {
            "sugar_per_100g": _n("Sugars, total including NLEA"),
            "calcium_per_100g": _n("Calcium, Ca"),
            "iron_per_100g": _n("Iron, Fe"),
            "potassium_per_100g": _n("Potassium, K"),
            "vitamin_c_per_100g": _n("Vitamin C, total ascorbic acid"),
        },
        "nutriscore_grade": None,
        "matched_name": food.get("description"),
        "usda_fdc_id": food.get("fdcId"),
        "raw_data": {"source": "usda", "fdc_id": food.get("fdcId"), "description": food.get("description")},
    }


# ── Claude Haiku fallback ─────────────────────────────────────────────────────

_LLM_PROMPT = """\
Estimate nutrients per 100g for this food sold on Swiggy Instamart India.
Item: {item_name}
Brand: {brand}
Quantity as sold: {qty_desc}

Return ONLY valid JSON. Use null for values you cannot reasonably estimate.
Use published nutritional data, ICMR tables, or USDA averages as your source.

Also identify:
- "food_concept": the canonical brand-stripped food, lowercase, e.g. "milk",
  "paneer", "dal" — not the product name or brand.
- "notable_nutrients": which of these this food is a meaningful SOURCE of —
  choose only from [{allowed_notable}], and only include a nutrient here if
  you also gave it a real (non-null) numeric value above. Do not include
  sodium, sugar, or saturated fat here — those are limits, not benefits.

{{
  "calories_per_100g": 0,
  "protein_per_100g": 0,
  "total_carbs_per_100g": 0,
  "sugar_per_100g": 0,
  "fat_per_100g": 0,
  "saturated_fat_per_100g": 0,
  "fiber_per_100g": 0,
  "sodium_mg_per_100g": 0,
  "calcium_per_100g": null,
  "iron_per_100g": null,
  "potassium_per_100g": null,
  "vitamin_c_per_100g": null,
  "vitamin_b12_per_100g": null,
  "vitamin_d_per_100g": null,
  "food_concept": "",
  "notable_nutrients": []
}}\
"""


async def _estimate_llm(item_name: str, brand: str | None, qty_desc: str) -> dict | None:
    from app.providers.factory import get_llm_provider
    try:
        prompt = _LLM_PROMPT.format(
            item_name=item_name,
            brand=brand or "generic",
            qty_desc=qty_desc or "unknown",
            allowed_notable=", ".join(sorted(_ALLOWED_NOTABLE_KEYS)),
        )
        provider = get_llm_provider()
        raw = await provider.complete(system="You are a nutritional data assistant. Return only valid JSON.", user=prompt, max_tokens=512)
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
    except Exception as e:
        logger.warning("llm_estimate_failed", item=item_name, error=str(e))
        return None

    # Some providers (seen on Groq/Llama) return syntactically valid JSON that
    # nonetheless omits every macro field — parsing succeeds, so this wasn't
    # caught above, but treating it as "resolved" would silently write a
    # confidently-labelled zero-calorie row. A genuine estimate should have at
    # least calories; if every core macro is missing, this wasn't a real
    # estimate and should be retried/left unresolved instead of cached as one.
    core_macros = ("calories_per_100g", "protein_per_100g", "total_carbs_per_100g", "fat_per_100g")
    if all(data.get(k) is None for k in core_macros):
        logger.warning("llm_estimate_empty", item=item_name, raw=raw[:200])
        return None

    nutrients = {
        "sugar_per_100g": data.get("sugar_per_100g"),
        "saturated_fat_per_100g": data.get("saturated_fat_per_100g"),
        "calcium_per_100g": data.get("calcium_per_100g"),
        "iron_per_100g": data.get("iron_per_100g"),
        "potassium_per_100g": data.get("potassium_per_100g"),
        "vitamin_c_per_100g": data.get("vitamin_c_per_100g"),
        "vitamin_b12_per_100g": data.get("vitamin_b12_per_100g"),
        "vitamin_d_per_100g": data.get("vitamin_d_per_100g"),
    }

    resolved = {
        "source": "llm",
        "confidence": "estimate",
        "serving_size_g": None,
        "calories_per_100g": data.get("calories_per_100g"),
        "protein_per_100g": data.get("protein_per_100g"),
        "total_carbs_per_100g": data.get("total_carbs_per_100g"),
        "fat_per_100g": data.get("fat_per_100g"),
        "fiber_per_100g": data.get("fiber_per_100g"),
        "sodium_mg_per_100g": data.get("sodium_mg_per_100g"),
        "nutrients": nutrients,
        "nutriscore_grade": None,
        "matched_name": None,
        "raw_data": {"source": "llm"},
    }

    # food_concept: trust the model's answer if it gave one; else mechanical fallback.
    raw_concept = data.get("food_concept")
    resolved["food_concept"] = (
        raw_concept.strip().lower() if isinstance(raw_concept, str) and raw_concept.strip()
        else _mechanical_food_concept(item_name, brand)
    )

    # notable_nutrients: validate against allowed vocab + the model's own non-null
    # values + the same floor used for OFF/USDA (defense against a claim
    # inconsistent with the numbers the model itself just gave).
    raw_notable = data.get("notable_nutrients")
    claimed = set(raw_notable) if isinstance(raw_notable, list) else set()
    validated = [
        k for k in claimed
        if k in _ALLOWED_NOTABLE_KEYS
        and _density_value(resolved, k) is not None
        and _density_value(resolved, k) >= _NOTABLE_FLOORS[k]
    ]
    resolved["notable_nutrients"] = validated or _mechanical_notable_nutrients(resolved)

    return resolved


# ── DB upsert ─────────────────────────────────────────────────────────────────

async def _db_upsert(db: AsyncSession, sku_id: str, quantity_g: float | None,
                     qty_unresolvable: bool, resolved: dict) -> NutritionCache:
    result = await db.execute(select(NutritionCache).where(NutritionCache.sku_id == sku_id))
    row = result.scalar_one_or_none()

    fields: dict[str, Any] = {
        "source": resolved["source"],
        "confidence": resolved["confidence"],
        "quantity_g": quantity_g,
        "quantity_unresolvable": qty_unresolvable,
        "serving_size_g": resolved.get("serving_size_g"),
        "calories_per_100g": resolved.get("calories_per_100g"),
        "protein_per_100g": resolved.get("protein_per_100g"),
        "total_carbs_per_100g": resolved.get("total_carbs_per_100g"),
        "fat_per_100g": resolved.get("fat_per_100g"),
        "fiber_per_100g": resolved.get("fiber_per_100g"),
        "sodium_mg_per_100g": resolved.get("sodium_mg_per_100g"),
        "nutrients": resolved.get("nutrients") or {},
        "nutriscore_grade": resolved.get("nutriscore_grade"),
        "matched_name": resolved.get("matched_name"),
        "off_product_id": resolved.get("off_product_id"),
        "usda_fdc_id": resolved.get("usda_fdc_id"),
        "raw_data": resolved.get("raw_data"),
        "food_concept": resolved.get("food_concept"),
        "notable_nutrients": resolved.get("notable_nutrients") or [],
    }

    if row:
        # Only overwrite if new confidence is higher
        _rank = {"verified": 4, "high": 3, "medium": 2, "estimate": 1}
        if _rank.get(resolved["confidence"], 0) >= _rank.get(row.confidence, 0):
            for k, v in fields.items():
                setattr(row, k, v)
    else:
        row = NutritionCache(id=__import__("uuid").uuid4().__str__(), sku_id=sku_id, **fields)
        db.add(row)

    await db.commit()
    await db.refresh(row)
    return row


# ── Public entry point ────────────────────────────────────────────────────────

async def resolve_item(
    db: AsyncSession,
    sku_id: str,
    item_name: str,
    brand: str | None,
    qty_desc: str,
) -> dict:
    """
    Resolve nutrition for a single order item. Returns a dict with the resolved
    fields (source, confidence, calories_per_100g, …) or unresolved sentinel.

    Resolution order: Redis → DB → OFF → USDA → Haiku.
    Always writes back to Redis + DB on a fresh resolution.
    """
    # 1. Redis hot cache
    cached = await _redis_get(sku_id)
    if cached:
        logger.debug("nutrition_cache_redis_hit", sku_id=sku_id)
        return cached

    # 2. DB cold cache
    result = await db.execute(select(NutritionCache).where(NutritionCache.sku_id == sku_id))
    db_row = result.scalar_one_or_none()
    if db_row:
        data = _row_to_dict(db_row)
        await _redis_set(sku_id, data)
        logger.debug("nutrition_cache_db_hit", sku_id=sku_id)
        return data

    # Quantity normalization (needed regardless of which source resolves macros)
    quantity_g = _parse_quantity_g(qty_desc, item_name)
    qty_unresolvable = quantity_g is None

    # 3. Open Food Facts
    resolved = await _search_off(item_name, brand)

    # 4. USDA
    if not resolved:
        resolved = await _search_usda(item_name)

    # 5. Claude Haiku
    if not resolved:
        resolved = await _estimate_llm(item_name, brand, qty_desc)

    if not resolved:
        unresolved = {
            "sku_id": sku_id,
            "source": "unresolved",
            "confidence": "unresolved",
            "quantity_g": quantity_g,
            "quantity_unresolvable": qty_unresolvable,
            "serving_size_g": None,
            "calories_per_100g": None,
            "protein_per_100g": None,
            "total_carbs_per_100g": None,
            "fat_per_100g": None,
            "fiber_per_100g": None,
            "sodium_mg_per_100g": None,
            "nutrients": {},
            "matched_name": None,
            "nutriscore_grade": None,
            "food_concept": None,
            "notable_nutrients": [],
        }
        # Don't cache unresolved in Redis long-term — let it retry next order
        return unresolved

    # Gap-to-Cart (B1): OFF/USDA rows don't set food_concept/notable_nutrients
    # themselves (only the LLM path does, inline in _estimate_llm) — mechanical
    # fallback here covers them uniformly. Also backstops an LLM row where
    # validation stripped everything.
    if not resolved.get("food_concept"):
        resolved["food_concept"] = _mechanical_food_concept(
            resolved.get("matched_name") or item_name, brand
        )
    if not resolved.get("notable_nutrients"):
        resolved["notable_nutrients"] = _mechanical_notable_nutrients(resolved)

    # Write to DB + Redis
    row = await _db_upsert(db, sku_id, quantity_g, qty_unresolvable, resolved)
    data = _row_to_dict(row)
    await _redis_set(sku_id, data)
    logger.info("nutrition_resolved", sku_id=sku_id, source=resolved["source"], confidence=resolved["confidence"])
    return data


def compute_item_totals(resolved: dict) -> dict:
    """
    Scale per-100g values by quantity_g to get absolute totals for this order item.
    Returns a dict with calories, protein_g, carbs_g, fat_g, fiber_g, sodium_mg.
    All None if quantity_unresolvable or any per-100g value is missing.
    """
    if resolved.get("quantity_unresolvable") or not resolved.get("quantity_g"):
        return {"calories": None, "protein_g": None, "carbs_g": None,
                "fat_g": None, "fiber_g": None, "sodium_mg": None}

    q = resolved["quantity_g"] / 100.0

    def _s(key: str) -> float | None:
        v = resolved.get(key)
        return round(v * q, 1) if v is not None else None

    nutrient_totals: dict[str, float | None] = {}
    for k, v in (resolved.get("nutrients") or {}).items():
        # keys like "sugar_per_100g" → "sugar_g"
        out_key = k.replace("_per_100g", "_g")
        nutrient_totals[out_key] = round(v * q, 1) if v is not None else None

    return {
        "calories": _s("calories_per_100g"),
        "protein_g": _s("protein_per_100g"),
        "carbs_g": _s("total_carbs_per_100g"),
        "fat_g": _s("fat_per_100g"),
        "fiber_g": _s("fiber_per_100g"),
        "sodium_mg": _s("sodium_mg_per_100g"),
        "nutrient_totals": nutrient_totals,
    }
