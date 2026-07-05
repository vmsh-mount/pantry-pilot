"""
LangGraph planning graph — the five-stage pipeline.

Graph topology:
  sense → plan_rules → plan_llm → optimize → confirm → place → END
                                                  ↓ (skip/abort)
                                                 END

Each node is an async function that receives PlanningState, mutates it,
and returns the updated state. LangGraph merges returned dicts into state.

The graph does NOT wait for WhatsApp confirmation — it sends the basket card
then exits. The PLACE stage is triggered by a separate Celery task when the
user confirms via WhatsApp (or by handle_confirmation_timeout if they don't).
This keeps the graph short-lived and avoids holding an async context open for
up to 6 hours.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Literal

from langgraph.graph import StateGraph, END

from app.agent.state import PlanningState, BasketItem
from app.config import get_settings
from app.utils.logging import get_logger

logger   = get_logger(__name__)
settings = get_settings()

# ── Diet / allergy hard-block keywords ───────────────────────────────────────
_NON_VEG_KEYWORDS = {
    "chicken", "mutton", "egg", "eggs", "fish", "prawn", "prawns",
    "meat", "beef", "pork", "lamb", "crab", "lobster", "seafood",
}
_JAIN_BLOCK = {"onion", "onions", "garlic", "potato", "potatoes", "carrot", "carrots"}

# ── Budget trim priority (lower = trim first) ─────────────────────────────────
_TRIM_PRIORITY = {"packaged": 1, "fresh_produce": 2, "dairy": 3, "staples": 4}

# ── Minimum basket size before LLM is asked to fill gaps ─────────────────────
_MIN_BASKET_SIZE = 8

# ── Recent order recency guard (days) ────────────────────────────────────────
_RECENCY_GUARD_DAYS = 3


# ══════════════════════════════════════════════════════════════════════════════
# Node 1 — SENSE
# ══════════════════════════════════════════════════════════════════════════════

async def sense(state: PlanningState) -> dict:
    """
    Gather household context: profile, pantry state (with decay), brand prefs,
    delivery address, WhatsApp number.
    """
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.db import (
        Household, HouseholdPreferences, BrandPreference,
    )
    from app.services.pantry_service import PantryService

    household_id = state["household_id"]
    loop_run_id  = state["loop_run_id"]

    logger.info("sense_start", household_id=household_id, loop_run_id=loop_run_id)

    async with AsyncSessionLocal() as db:
        # Core household fields
        hh_result = await db.execute(
            select(Household).where(Household.id == household_id)
        )
        household = hh_result.scalar_one_or_none()

        if household is None:
            return {
                "error":        f"Household {household_id} not found",
                "error_stage":  "sense",
                "should_abort": True,
            }

        # Preferences
        prefs_result = await db.execute(
            select(HouseholdPreferences).where(
                HouseholdPreferences.household_id == household_id
            )
        )
        prefs = prefs_result.scalar_one_or_none()

        # Resolve swiggy_address_id from our addresses table
        swiggy_address_id = None
        if prefs and prefs.preferred_address_id:
            from app.models.db import Address
            addr_result = await db.execute(
                select(Address).where(Address.id == prefs.preferred_address_id)
            )
            addr = addr_result.scalar_one_or_none()
            if addr:
                swiggy_address_id = addr.swiggy_address_id
            logger.info("sense_address_resolved", preferred_address_id=str(prefs.preferred_address_id), addr_found=addr is not None, swiggy_address_id=swiggy_address_id)

        # Brand preferences
        bp_result = await db.execute(
            select(BrandPreference).where(
                BrandPreference.household_id == household_id
            )
        )
        brand_rows = bp_result.scalars().all()
        brand_prefs = {b.item_name: b.preferred_brand for b in brand_rows}

        # Pantry state with consumption decay applied
        pantry_svc   = PantryService(db)
        pantry_items = await pantry_svc.apply_decay(household_id)

        pantry_serialised = [
            {
                "item_name":               p.item_name,
                "category":                p.category,
                "unit":                    p.standard_unit,
                "estimated_qty_remaining": float(p.estimated_qty_remaining or 0),
                "reorder_threshold":       float(p.reorder_threshold or 0),
                "last_ordered_qty":        float(p.last_ordered_qty or 1),
                "avg_weekly_consumption":  float(p.avg_weekly_consumption or 0.5),
                "times_ordered":           p.times_ordered or 0,
            }
            for p in pantry_items
        ]

        # If pantry is empty, use Swiggy's go-to items to bootstrap the basket
        recent_orders: list[dict] = []
        if not pantry_serialised:
            try:
                from app.providers.factory import get_mcp_provider
                client = get_mcp_provider(state["access_token"])
                # Need Swiggy's address ID (not our internal UUID) for MCP calls
                addr_id = swiggy_address_id
                if not addr_id:
                    addresses = await client.get_addresses()
                    def _is_default(a):
                        return a.get("is_default", False) if isinstance(a, dict) else getattr(a, "is_default", False)
                    def _addr_id(a):
                        return a.get("id") if isinstance(a, dict) else getattr(a, "id", None)
                    home = next((a for a in addresses if _is_default(a)), addresses[0] if addresses else None)
                    addr_id = _addr_id(home) if home else None
                if addr_id:
                    go_to_items = await client.your_go_to_items(addr_id)
                    recent_orders = []
                    for item in go_to_items:
                        # Support both object-style (real Swiggy) and dict-style (mock) responses
                        def _g(obj, key, default=None):
                            return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
                        if not _g(item, "in_stock", True):
                            continue
                        recent_orders.append({
                            "item_name":   _g(item, "name") or _g(item, "item_name", ""),
                            "sku_id":      _g(item, "sku_id"),
                            "brand":       _g(item, "brand"),
                            "category":    _g(item, "category", "grocery"),
                            "unit":        _g(item, "unit", "units"),
                            "qty":         _g(item, "quantity", 1),
                            "unit_price":  _g(item, "price") or _g(item, "unit_price", 0),
                            "order_count": _g(item, "order_count", 1),
                        })
                    logger.info("sense_go_to_items_fallback", household_id=household_id, items=len(recent_orders))
            except Exception as e:
                logger.warning("sense_go_to_items_failed", error=str(e))

        # Update loop run state in DB
        from app.models.db import LoopRun
        from sqlalchemy import update
        await db.execute(
            update(LoopRun)
            .where(LoopRun.id == loop_run_id)
            .values(
                state              = "sensing",
                sense_started_at   = datetime.now(timezone.utc),
            )
        )
        await db.commit()

    now = datetime.now(timezone.utc)
    week_label = f"Week of {now.strftime('%d %b %Y')}"

    budget_mid = (
        (household.weekly_budget_min or 1500) +
        (household.weekly_budget_max or 2500)
    ) / 2

    profile = {
        "member_count":  household.member_count,
        "diet_type":     household.diet_type,
        "allergies":     household.allergies or [],
        "budget_min":    household.weekly_budget_min or 1500,
        "budget_max":    household.weekly_budget_max or 2500,
        "budget_mid":    budget_mid,
        "city":          household.city,
    }

    logger.info(
        "sense_complete",
        household_id    = household_id,
        pantry_items    = len(pantry_serialised),
        brand_prefs     = len(brand_prefs),
    )

    return {
        "household_profile":       profile,
        "pantry_items":            pantry_serialised,
        "recent_orders":           recent_orders,
        "brand_preferences":       brand_prefs,
        "preferred_address_id":    prefs.preferred_address_id if prefs else None,
        "swiggy_address_id":       swiggy_address_id,
        "preferred_delivery_slot": prefs.preferred_delivery_slot if prefs else "evening",
        "whatsapp_number":         household.whatsapp_number or "",
        "week_label":              week_label,
        "should_abort":            False,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Node 2a — PLAN: Rules Engine
# ══════════════════════════════════════════════════════════════════════════════

async def plan_rules(state: PlanningState) -> dict:
    """
    Deterministic rules engine. Produces the basket skeleton.

    Rules applied in order:
      1. Reorder depleted items (estimated_qty < reorder_threshold)
      2. Diet / allergy hard-block
      3. Recency guard (ordered in last 3 days → skip)
      4. Budget pre-check (flag items over budget)
    """
    if state.get("should_abort"):
        return {}

    logger.info("plan_rules_start", household_id=state["household_id"])

    pantry_items   = state.get("pantry_items", [])
    recent_orders  = state.get("recent_orders", [])
    profile        = state.get("household_profile", {})
    diet_type      = profile.get("diet_type", "vegetarian")
    allergies      = {a.lower() for a in profile.get("allergies", [])}

    candidate: list[BasketItem] = []

    # If pantry is empty, bootstrap from go-to items — treat all as needing reorder
    if not pantry_items and recent_orders:
        logger.info("plan_rules_go_to_fallback", household_id=state["household_id"], items=len(recent_orders))
        # Go-to items are already resolved SKUs — add directly to candidate, skip optimize search
        for it in recent_orders:
            name_lc = it["item_name"].lower()
            if diet_type in ("vegetarian", "vegan", "jain") and any(kw in name_lc for kw in _NON_VEG_KEYWORDS):
                continue
            if diet_type == "jain" and any(kw in name_lc for kw in _JAIN_BLOCK):
                continue
            if any(allergen in name_lc for allergen in allergies):
                continue
            candidate.append({
                "item_name":           it["item_name"],
                "sku_id":              it.get("sku_id"),
                "product_name":        it["item_name"],
                "brand":               it.get("brand") or state.get("brand_preferences", {}).get(it["item_name"]),
                "category":            it.get("category", "grocery"),
                "quantity":            float(it.get("qty", 1)),
                "unit":                it.get("unit", "units"),
                "unit_price":          it.get("unit_price"),
                "total_price":         it.get("unit_price"),
                "in_stock":            True,
                "added_by":            "go_to_items",
                "add_reason":          f"Your go-to item (ordered {it.get('order_count', 1)}x)",
                "is_substitution":     False,
                "original_item_name":  None,
                "substitution_reason": None,
            })
        # Skip the pantry loop below — nothing to iterate
        pantry_items = []

    for item in pantry_items:
        name     = item["item_name"]
        name_lc  = name.lower()

        # ── Rule 2: Diet / allergy hard-block ────────────────────────────────
        if diet_type in ("vegetarian", "vegan", "jain"):
            if any(kw in name_lc for kw in _NON_VEG_KEYWORDS):
                continue

        if diet_type == "jain":
            if any(kw in name_lc for kw in _JAIN_BLOCK):
                continue

        if any(allergen in name_lc for allergen in allergies):
            continue

        # ── Rule 1: Reorder if below threshold ───────────────────────────────
        remaining = item["estimated_qty_remaining"]
        threshold = item["reorder_threshold"]

        if remaining <= threshold:
            qty = item["last_ordered_qty"] or 1.0

            candidate.append({
                "item_name":          name,
                "sku_id":             None,
                "product_name":       None,
                "brand":              state.get("brand_preferences", {}).get(name),
                "category":           item.get("category", "grocery"),
                "quantity":           qty,
                "unit":               item.get("unit", "unit"),
                "unit_price":         None,
                "total_price":        None,
                "in_stock":           True,
                "added_by":           "rules_engine",
                "add_reason":         f"Stock at {remaining:.2f}{item['unit']} ≤ threshold {threshold:.2f}{item['unit']}",
                "is_substitution":    False,
                "original_item_name": None,
                "substitution_reason": None,
            })

    # Update LoopRun state
    async with _db_context() as db:
        from app.models.db import LoopRun
        from sqlalchemy import update
        await db.execute(
            update(LoopRun)
            .where(LoopRun.id == state["loop_run_id"])
            .values(
                state             = "planning",
                plan_started_at   = datetime.now(timezone.utc),
            )
        )
        await db.commit()

    logger.info(
        "plan_rules_complete",
        household_id     = state["household_id"],
        candidate_count  = len(candidate),
    )

    return {"candidate_basket": candidate}


# ══════════════════════════════════════════════════════════════════════════════
# Node 2b — PLAN: LLM Intelligent Layer
# ══════════════════════════════════════════════════════════════════════════════

async def plan_llm(state: PlanningState) -> dict:
    """
    LLM layer: gap-fill, variety, seasonal awareness, quantity rationalisation.
    Uses Claude via Anthropic SDK (structured JSON output).
    """
    if state.get("should_abort"):
        return {}

    household_id = state["household_id"]
    profile      = state.get("household_profile", {})
    candidate    = state.get("candidate_basket", [])

    logger.info("plan_llm_start", household_id=household_id, candidate_items=len(candidate))

    basket_is_thin = len(candidate) < _MIN_BASKET_SIZE

    # Build prompt
    candidate_json = json.dumps(
        [{"item": i["item_name"], "qty": i["quantity"], "unit": i["unit"],
          "category": i["category"]} for i in candidate],
        indent=2
    )

    now   = datetime.now(timezone.utc)
    month = now.strftime("%B")

    system_prompt = (
        "You are a household grocery planner for Indian households. "
        "You help plan weekly grocery baskets that are practical, budget-conscious, "
        "and appropriate for the household's dietary preferences. "
        "You have deep knowledge of Indian grocery staples, seasonal produce, "
        "and typical Indian household consumption patterns."
    )

    user_prompt = f"""
You are helping plan a weekly grocery basket for this household:

- Household size: {profile.get('member_count', 2)} people
- Diet: {profile.get('diet_type', 'vegetarian')}
- Allergies: {', '.join(profile.get('allergies', [])) or 'None'}
- Weekly budget: ₹{int(profile.get('budget_min', 1500))}–₹{int(profile.get('budget_max', 2500))}
- City: {profile.get('city', 'Bengaluru')}
- Current month: {month}

The rules engine has already generated this basket skeleton:
{candidate_json}

{'The basket is thin (fewer than 8 items). Please help fill gaps.' if basket_is_thin else ''}

Your tasks:
1. Identify any essential category gaps (e.g. no vegetables, no cooking oil).
2. Suggest up to 4 additional items if budget allows — use generic names like "fresh tomatoes 1kg", not brand names.
3. Note any seasonal produce available in {month} that would suit this household.
4. Flag any items that seem unusual for this diet type.

IMPORTANT rules:
- Do NOT suggest meat, fish, or eggs for a vegetarian/vegan/jain diet.
- Do NOT suggest onions, garlic, or root vegetables for a jain diet.
- Keep suggestions practical and commonly available on Swiggy Instamart.
- Stay within the budget. Prefer suggestions under ₹150 each.
- Use ONLY generic descriptions — no brand names in your suggestions.

Respond with ONLY valid JSON in this exact format:
{{
  "additions": [
    {{
      "item_name": "fresh tomatoes",
      "quantity": 1.0,
      "unit": "kg",
      "category": "fresh_produce",
      "reason": "No vegetables in current basket"
    }}
  ],
  "flags": ["Note 1", "Note 2"],
  "seasonal_note": "July is peak season for mangoes and drumsticks in South India."
}}

If no additions are needed, return an empty additions list.
"""

    from app.providers.factory import get_llm_provider
    llm = get_llm_provider()
    start_ms = int(time.monotonic() * 1000)

    try:
        raw_response = await llm.complete(
            system=system_prompt,
            user=user_prompt,
            max_tokens=1024,
        )
    except Exception as e:
        logger.warning("plan_llm_failed", household_id=household_id, error=str(e))
        # LLM failure is non-fatal — proceed with rules-only basket
        return {
            "llm_additions":  [],
            "llm_flags":      [f"LLM unavailable: {str(e)[:80]}"],
            "llm_tokens_used": 0,
            "llm_latency_ms":  0,
        }

    latency_ms  = int(time.monotonic() * 1000) - start_ms
    tokens_used = 0  # token count not universally available across providers

    logger.info(
        "plan_llm_complete",
        household_id = household_id,
        latency_ms   = latency_ms,
        tokens_used  = tokens_used,
    )

    # Parse LLM JSON
    llm_additions: list[BasketItem] = []
    llm_flags: list[str]            = []

    try:
        # Extract JSON from response (handle markdown code fences if present)
        json_str = raw_response.strip()
        if json_str.startswith("```"):
            json_str = json_str.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]

        parsed = json.loads(json_str)

        for addition in parsed.get("additions", []):
            name_lc = addition.get("item_name", "").lower()

            # Re-apply diet filter on LLM suggestions (safety net)
            profile_diet = profile.get("diet_type", "vegetarian")
            if profile_diet in ("vegetarian", "vegan", "jain"):
                if any(kw in name_lc for kw in _NON_VEG_KEYWORDS):
                    continue
            if profile_diet == "jain":
                if any(kw in name_lc for kw in _JAIN_BLOCK):
                    continue

            llm_additions.append({
                "item_name":          addition["item_name"],
                "sku_id":             None,
                "product_name":       None,
                "brand":              None,
                "category":           addition.get("category", "grocery"),
                "quantity":           float(addition.get("quantity", 1.0)),
                "unit":               addition.get("unit", "unit"),
                "unit_price":         None,
                "total_price":        None,
                "in_stock":           True,
                "added_by":           "llm",
                "add_reason":         addition.get("reason", "LLM suggestion"),
                "is_substitution":    False,
                "original_item_name": None,
                "substitution_reason": None,
            })

        llm_flags = parsed.get("flags", [])
        seasonal  = parsed.get("seasonal_note")
        if seasonal:
            llm_flags.append(seasonal)

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("plan_llm_parse_error", household_id=household_id, error=str(e))
        llm_flags = ["LLM response could not be parsed — using rules basket only."]

    return {
        "llm_additions":   llm_additions,
        "llm_flags":       llm_flags,
        "llm_tokens_used": tokens_used,
        "llm_latency_ms":  latency_ms,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Node 3 — OPTIMIZE
# ══════════════════════════════════════════════════════════════════════════════

async def optimize(state: PlanningState) -> dict:
    """
    Resolve candidate basket items against live Instamart catalogue.
    Handles SKU resolution, out-of-stock substitution, and budget reconciliation.
    """
    if state.get("should_abort"):
        return {}

    from app.providers.factory import get_mcp_provider
    from app.utils.exceptions import SwiggyMCPError

    household_id = state["household_id"]
    address_id   = state.get("swiggy_address_id")
    profile      = state.get("household_profile", {})
    brand_prefs  = state.get("brand_preferences", {})

    # Fallback: fetch addresses from Swiggy and pick the home/default one
    if not address_id:
        try:
            client    = get_mcp_provider(state["access_token"])
            addresses = await client.get_addresses()
            home       = next((a for a in addresses if (a.get("is_default", False) if isinstance(a, dict) else getattr(a, "is_default", False))), addresses[0] if addresses else None)
            address_id = (home.get("id") if isinstance(home, dict) else getattr(home, "id", None)) if home else None
            logger.info("optimize_address_fallback", household_id=household_id, address_id=address_id)
        except Exception as e:
            logger.warning("optimize_address_fallback_failed", error=str(e))

    if not address_id:
        return {
            "error":        "No delivery address configured",
            "error_stage":  "optimize",
            "should_abort": True,
        }

    # Merge candidate + LLM additions
    all_items: list[dict] = (
        list(state.get("candidate_basket", [])) +
        list(state.get("llm_additions", []))
    )

    client           = get_mcp_provider(state["access_token"])
    resolved_basket: list[BasketItem] = []
    items_unavailable: list[str]      = []
    substitutions_made                = 0

    logger.info("optimize_start", household_id=household_id, items_to_resolve=len(all_items))

    async with _db_context() as db:
        from app.models.db import LoopRun
        from sqlalchemy import update
        await db.execute(
            update(LoopRun)
            .where(LoopRun.id == state["loop_run_id"])
            .values(
                state                = "optimizing",
                optimize_started_at  = datetime.now(timezone.utc),
            )
        )
        await db.commit()

    for item in all_items:
        item_name = item["item_name"]
        diet_type = profile.get("diet_type", "vegetarian")

        # Build search query — use brand preference if available
        preferred_brand = brand_prefs.get(item_name) or item.get("brand")
        query = f"{preferred_brand} {item_name}" if preferred_brand else item_name

        best_match = None
        is_sub     = False
        sub_reason = None

        def _pattr(p, key, default=None):
            return p.get(key, default) if isinstance(p, dict) else getattr(p, key, default)

        def _norm_products(r):
            return r if isinstance(r, list) else getattr(r, "products", [])

        try:
            result = await client.search_products(query, address_id, limit=5)
            products = _norm_products(result)
            logger.info("optimize_search_result", item=item_name, query=query, product_count=len(products), products=[_pattr(p, "name", _pattr(p, "item_name", "?")) for p in products[:3]])

            # Filter: in-stock first
            in_stock = [p for p in products if _pattr(p, "in_stock", True)]
            stock_to_use = in_stock if in_stock else products

            # Pick best: exact brand if preferred, else best price
            if preferred_brand and in_stock:
                brand_match = next(
                    (p for p in in_stock if preferred_brand.lower() in (_pattr(p, "brand") or "").lower()),
                    None,
                )
                best_match = brand_match or in_stock[0]
            elif stock_to_use:
                best_match = stock_to_use[0]

            # If nothing in stock, try substitute (generic search)
            if best_match and not _pattr(best_match, "in_stock", True):
                fallback = await client.search_products(item_name, address_id, limit=5)
                fb_in_stock = [p for p in _norm_products(fallback) if _pattr(p, "in_stock", True)]

                # Apply diet filter to substitutes
                fb_in_stock = [
                    p for p in fb_in_stock
                    if not _violates_diet(_pattr(p, "name", ""), diet_type)
                ]

                if fb_in_stock:
                    best_match  = fb_in_stock[0]
                    is_sub      = True
                    sub_reason  = f"Original product out of stock; using {_pattr(best_match, 'name')}"
                    substitutions_made += 1
                else:
                    items_unavailable.append(item_name)
                    continue

        except (SwiggyMCPError, Exception) as e:
            logger.warning("optimize_search_failed", item=item_name, error=str(e))
            items_unavailable.append(item_name)
            continue

        if best_match is None:
            items_unavailable.append(item_name)
            continue

        qty        = float(item["quantity"])
        unit_price = _pattr(best_match, "price", 0)
        resolved_basket.append({
            "item_name":           item_name,
            "sku_id":              _pattr(best_match, "sku_id", _pattr(best_match, "id")),
            "product_name":        _pattr(best_match, "name"),
            "brand":               _pattr(best_match, "brand"),
            "category":            item.get("category", "grocery"),
            "quantity":            qty,
            "unit":                item.get("unit", "unit"),
            "unit_price":          unit_price,
            "total_price":         round(unit_price * qty, 2),
            "in_stock":            _pattr(best_match, "in_stock", True),
            "added_by":            item.get("added_by", "rules_engine"),
            "add_reason":          item.get("add_reason"),
            "is_substitution":     is_sub,
            "original_item_name":  item_name if is_sub else None,
            "substitution_reason": sub_reason,
        })

    # Budget reconciliation — trim until within budget_max
    budget_max   = profile.get("budget_max", 2500)
    estimated_total = sum(i["total_price"] for i in resolved_basket)

    if estimated_total > budget_max:
        # Sort by trim priority (snacks/packaged first)
        resolved_basket.sort(
            key=lambda x: _TRIM_PRIORITY.get(x["category"], 3),
            reverse=False  # lowest priority number = trim first
        )
        while estimated_total > budget_max and resolved_basket:
            removed = resolved_basket.pop(0)
            estimated_total -= removed["total_price"]
            logger.info(
                "budget_trim",
                household_id = household_id,
                removed_item = removed["item_name"],
                new_total    = estimated_total,
            )

    logger.info(
        "optimize_complete",
        household_id       = household_id,
        resolved_count     = len(resolved_basket),
        substitutions_made = substitutions_made,
        unavailable_count  = len(items_unavailable),
        estimated_total    = estimated_total,
    )

    return {
        "resolved_basket":    resolved_basket,
        "substitutions_made": substitutions_made,
        "items_unavailable":  items_unavailable,
        "estimated_total":    round(estimated_total, 2),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Node 4 — CONFIRM
# ══════════════════════════════════════════════════════════════════════════════

async def confirm(state: PlanningState) -> dict:
    """
    Send basket preview card to WhatsApp and exit.
    The graph pauses here — PLACE is triggered externally when the user confirms.
    """
    if state.get("should_abort"):
        return {}

    from app.services.whatsapp_service import (
        WhatsAppService, build_basket_summary,
    )

    household_id  = state["household_id"]
    loop_run_id   = state["loop_run_id"]
    resolved      = state.get("resolved_basket", [])
    phone         = state.get("whatsapp_number", "")
    profile       = state.get("household_profile", {})

    if not phone:
        return {
            "error":        "No WhatsApp number for household",
            "error_stage":  "confirm",
            "should_abort": True,
        }

    if not resolved:
        # Nothing to order — mark as skipped, no WhatsApp noise
        async with _db_context() as db:
            from app.models.db import LoopRun
            from sqlalchemy import update
            await db.execute(
                update(LoopRun)
                .where(LoopRun.id == loop_run_id)
                .values(state="skipped", skip_reason="empty_basket")
            )
            await db.commit()
        logger.info("confirm_skipped_empty_basket", household_id=household_id)
        return {"should_abort": True}

    now = datetime.now(timezone.utc)

    # Build basket summary for WhatsApp template
    basket_summary  = build_basket_summary(resolved, max_visible=5)
    estimated_total = state.get("estimated_total", 0)
    budget_mid      = profile.get("budget_mid", 2000)

    wa = WhatsAppService()
    await wa.send_basket_preview(
        phone_number   = phone,
        basket_summary = basket_summary,
        basket_total   = estimated_total,
        weekly_budget  = budget_mid,
    )

    # Update loop run state
    async with _db_context() as db:
        from app.models.db import LoopRun
        from sqlalchemy import update
        await db.execute(
            update(LoopRun)
            .where(LoopRun.id == loop_run_id)
            .values(
                state                = "awaiting_confirmation",
                optimize_completed_at = now,
                confirm_sent_at      = now,
                substitutions_count  = state.get("substitutions_made", 0),
                items_unavailable    = len(state.get("items_unavailable", [])),
                llm_tokens_used      = state.get("llm_tokens_used", 0),
                llm_latency_ms       = state.get("llm_latency_ms", 0),
            )
        )
        # Save resolved basket items
        from app.models.db import LoopRunItem
        for item in resolved:
            db.add(LoopRunItem(
                loop_run_id         = loop_run_id,
                household_id        = household_id,
                item_name           = item["item_name"],
                swiggy_sku_id       = item.get("sku_id"),
                swiggy_product_name = item.get("product_name"),
                brand               = item.get("brand"),
                quantity            = item["quantity"],
                unit                = item["unit"],
                unit_price          = item.get("unit_price"),
                total_price         = item.get("total_price"),
                added_by            = item.get("added_by", "rules_engine"),
                add_reason          = item.get("add_reason"),
                is_substitution     = item.get("is_substitution", False),
                original_item_name  = item.get("original_item_name"),
                substitution_reason = item.get("substitution_reason"),
            ))
        await db.commit()

    # Schedule timeout task — auto-skip after 6 hours
    from app.tasks.planning import handle_confirmation_timeout
    handle_confirmation_timeout.apply_async(
        args    = [household_id, loop_run_id],
        countdown = 6 * 3600,
    )

    logger.info(
        "confirm_sent",
        household_id    = household_id,
        loop_run_id     = loop_run_id,
        basket_items    = len(resolved),
        estimated_total = estimated_total,
    )

    return {
        "confirmation_sent_at": now.isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Node 5 — PLACE
# ══════════════════════════════════════════════════════════════════════════════

async def place(state: PlanningState) -> dict:
    """
    Place the confirmed order via Swiggy MCP.
    Called by PlanningService.run_place() — NOT part of the main graph run.
    Separated so the graph can exit after CONFIRM without holding a long session.
    """
    from app.providers.factory import get_mcp_provider
    from app.utils.exceptions import (
        SwiggyMCPError, CheckoutFailedError, CartPriceMismatchError,
        TokenExpiredError,
    )

    household_id    = state["household_id"]
    loop_run_id     = state["loop_run_id"]
    address_id      = state.get("swiggy_address_id")
    delivery_slot   = state.get("preferred_delivery_slot", "evening")
    resolved_basket = state.get("resolved_basket") or state.get("user_edited_basket", [])
    expected_total  = state.get("estimated_total", 0)

    client = get_mcp_provider(state["access_token"])

    if not address_id:
        # Fallback: fetch live from Swiggy and persist for next time
        try:
            addresses = await client.get_addresses()
            def _is_default(a):
                return a.get("is_default", False) if isinstance(a, dict) else getattr(a, "is_default", False)
            def _addr_id(a):
                return a.get("id") if isinstance(a, dict) else getattr(a, "id", None)
            home = next((a for a in addresses if _is_default(a)), addresses[0] if addresses else None)
            address_id = _addr_id(home) if home else None
            if address_id:
                async with _db_context() as db:
                    from app.models.db import HouseholdPreferences
                    from sqlalchemy import select
                    prefs_res = await db.execute(
                        select(HouseholdPreferences).where(
                            HouseholdPreferences.household_id == household_id
                        )
                    )
                    prefs = prefs_res.scalar_one_or_none()
                    if prefs and not prefs.preferred_address_id:
                        from app.services.id_mapper import ExternalIdMapper
                        internal_id = await ExternalIdMapper.get_or_create_internal_id(
                            db, "address", "swiggy", address_id,
                            household_id=household_id,
                        )
                        prefs.preferred_address_id = internal_id
                        await db.commit()
        except Exception as e:
            logger.warning("place_address_fallback_failed", household_id=household_id, error=str(e))

    if not address_id:
        return {
            "error": "No delivery address", "error_stage": "place", "should_abort": True
        }

    logger.info("place_start", household_id=household_id, item_count=len(resolved_basket))

    async with _db_context() as db:
        from app.models.db import LoopRun
        from sqlalchemy import update
        await db.execute(
            update(LoopRun)
            .where(LoopRun.id == loop_run_id)
            .values(
                state            = "placing",
                place_started_at = datetime.now(timezone.utc),
            )
        )
        await db.commit()

    try:
        from app.config import get_settings as _get_settings

        def _rattr(obj, key, default=None):
            return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

        if _get_settings().pantrypilot_dry_run:
            # Skip all real Swiggy cart mutations — use expected total directly.
            actual_total = expected_total
            order = await client.checkout(
                address_id      = address_id,
                delivery_slot   = delivery_slot,
                estimated_total = expected_total,
            )
        else:
            # Step 1 — clear then build cart
            await client.clear_cart()

            cart_items = [
                {"sku_id": item["sku_id"], "quantity": int(item["quantity"])}
                for item in resolved_basket
                if item.get("sku_id")
            ]
            cart = await client.update_cart(cart_items, address_id=address_id)

            # Step 2 — price tolerance check (±5%)
            actual_total = _rattr(cart, "grand_total", _rattr(cart, "total", 0))
            if expected_total > 0 and actual_total > 0:
                drift = abs(actual_total - expected_total) / expected_total
                if drift > 0.05:
                    raise CartPriceMismatchError(
                        f"Cart total Rs{actual_total:.0f} differs from expected "
                        f"Rs{expected_total:.0f} by {drift*100:.1f}% (limit 5%)"
                    )

            # Step 3 — checkout
            order = await client.checkout(
                address_id      = address_id,
                delivery_slot   = delivery_slot,
                estimated_total = expected_total,
            )

    except TokenExpiredError:
        raise   # bubble up — Celery task will catch and send re-auth

    except (SwiggyMCPError, CheckoutFailedError, CartPriceMismatchError) as e:
        logger.error("place_failed", household_id=household_id, error=str(e))
        async with _db_context() as db:
            from app.models.db import LoopRun
            from sqlalchemy import update
            await db.execute(
                update(LoopRun)
                .where(LoopRun.id == loop_run_id)
                .values(
                    state          = "failed",
                    failure_reason = str(e),
                    failure_stage  = "place",
                )
            )
            await db.commit()

        # Notify the user — they confirmed this basket and deserve to know it failed.
        whatsapp_number = state.get("whatsapp_number")
        if whatsapp_number:
            error_str = str(e).lower()
            if "partially available" in error_str:
                msg = (
                    "Your order couldn't be placed — one or more items you confirmed "
                    "are now low in stock and couldn't be added at the requested quantity. "
                    "Please edit your basket and confirm again."
                )
            elif "price mismatch" in error_str:
                msg = (
                    "Your order couldn't be placed — prices changed between confirmation "
                    "and checkout. Please review your basket and confirm again."
                )
            else:
                msg = (
                    "Your order couldn't be placed due to a checkout error. "
                    "Please try confirming again or contact support."
                )
            try:
                from app.services.whatsapp_service import WhatsAppService
                wa = WhatsAppService()
                await wa.send_text(whatsapp_number, msg)
            except Exception as wa_err:
                logger.warning("place_failed_wa_notify_error", error=str(wa_err))

        return {
            "error": str(e), "error_stage": "place", "should_abort": True
        }

    # ── Success ───────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)

    async with _db_context() as db:
        from app.models.db import LoopRun, Order, OrderItem
        from sqlalchemy import update as sa_update

        # Create Order record
        db_order = Order(
            household_id       = household_id,
            loop_run_id        = loop_run_id,
            swiggy_order_id    = _rattr(order, "order_id"),
            swiggy_address_id  = address_id,
            delivery_slot      = delivery_slot,
            item_total         = _rattr(cart, "item_total", actual_total),
            delivery_fee       = _rattr(cart, "delivery_fee", 0),
            taxes              = _rattr(cart, "taxes", 0),
            grand_total        = actual_total,
            status             = _rattr(order, "status", "PLACED"),
        )
        db.add(db_order)
        await db.flush()   # get db_order.id

        for item in resolved_basket:
            db.add(OrderItem(
                order_id       = db_order.id,
                household_id   = household_id,
                swiggy_sku_id  = item.get("sku_id", ""),
                product_name   = item.get("product_name") or item["item_name"],
                brand          = item.get("brand"),
                quantity       = item["quantity"],
                unit           = item.get("unit", "unit"),
                unit_price     = item.get("unit_price", 0),
                total_price    = item.get("total_price", 0),
            ))

        # Mark loop run complete
        await db.execute(
            sa_update(LoopRun)
            .where(LoopRun.id == loop_run_id)
            .values(
                state               = "completed",
                place_completed_at  = now,
                order_id            = db_order.id,
            )
        )
        await db.commit()

    order_id_str = _rattr(order, "order_id", "MOCK")

    # Send WhatsApp receipt
    try:
        from app.services.whatsapp_service import WhatsAppService
        wa = WhatsAppService()
        receipt_order_id = f"[DRY RUN] {order_id_str}" if settings.pantrypilot_dry_run else order_id_str
        await wa.send_order_receipt(
            phone_number       = state.get("whatsapp_number", ""),
            item_count         = len(resolved_basket),
            grand_total        = actual_total,
            delivery_area      = "",   # resolved from address label at runtime
            estimated_delivery = _rattr(order, "estimated_delivery", _rattr(order, "estimated_delivery", "Today")) or "Today",
            swiggy_order_id    = receipt_order_id,
        )
    except Exception as e:
        logger.warning("receipt_whatsapp_failed", error=str(e))

    # Schedule pantry update (30 min after placement)
    from app.tasks.pantry import update_pantry_post_order
    update_pantry_post_order.apply_async(
        args      = [household_id, order_id_str],
        countdown = 30 * 60,
    )

    logger.info(
        "place_complete",
        household_id    = household_id,
        swiggy_order_id = order_id_str,
        grand_total     = actual_total,
    )

    return {
        "swiggy_order_id": order_id_str,
        "final_total":     actual_total,
        "estimated_delivery": _rattr(order, "estimated_delivery", "Today"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Router — abort check
# ══════════════════════════════════════════════════════════════════════════════

def should_continue(state: PlanningState) -> Literal["continue", "abort"]:
    """Edge condition: abort if any stage set should_abort=True."""
    if state.get("should_abort"):
        return "abort"
    return "continue"


# ══════════════════════════════════════════════════════════════════════════════
# Graph assembly
# ══════════════════════════════════════════════════════════════════════════════

def build_planning_graph() -> StateGraph:
    """
    Assemble the LangGraph StateGraph for the planning pipeline.

    Nodes: sense → plan_rules → plan_llm → optimize → confirm
    (place is called separately by PlanningService.run_place())
    """
    graph = StateGraph(PlanningState)

    graph.add_node("sense",      sense)
    graph.add_node("plan_rules", plan_rules)
    graph.add_node("plan_llm",   plan_llm)
    graph.add_node("optimize",   optimize)
    graph.add_node("confirm",    confirm)

    # Edges
    graph.set_entry_point("sense")

    graph.add_conditional_edges(
        "sense",
        should_continue,
        {"continue": "plan_rules", "abort": END},
    )
    graph.add_conditional_edges(
        "plan_rules",
        should_continue,
        {"continue": "plan_llm", "abort": END},
    )
    graph.add_conditional_edges(
        "plan_llm",
        should_continue,
        {"continue": "optimize", "abort": END},
    )
    graph.add_conditional_edges(
        "optimize",
        should_continue,
        {"continue": "confirm", "abort": END},
    )
    graph.add_edge("confirm", END)

    return graph.compile()


# ── Private helpers ───────────────────────────────────────────────────────────

def _violates_diet(product_name: str, diet_type: str) -> bool:
    """Return True if a product name violates the given diet constraint."""
    name_lc = product_name.lower()
    if diet_type in ("vegetarian", "vegan", "jain"):
        if any(kw in name_lc for kw in _NON_VEG_KEYWORDS):
            return True
    if diet_type == "jain":
        if any(kw in name_lc for kw in _JAIN_BLOCK):
            return True
    return False


from contextlib import asynccontextmanager

@asynccontextmanager
async def _db_context():
    """Thin helper so nodes can open a DB session without importing at module level."""
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        yield db
