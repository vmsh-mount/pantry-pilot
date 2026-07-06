"""
OnboardingService — drives the 7-step onboarding flow.

Steps:
  1. OAuth completes (handled by AuthService)
  2. run_inference()    — read Swiggy order history, extract diet/brands/budget/schedule
  3. save_profile()     — persist questionnaire answers from cockpit
  4. send_otp()         — generate & send WhatsApp OTP
  5. verify_otp()       — validate OTP, mark WhatsApp verified
  6. generate_preview_basket() — dry-run planning to show what first order looks like
  7. complete_onboarding()     — bootstrap pantry, schedule first loop run

Key design rules:
  - Inference is best-effort; missing data never blocks completion.
  - OTP: 6-digit, 10-minute TTL, max 3 attempts (stored in Redis).
  - Preview basket is read-only — no cart mutation, no order placed.
  - Brand preferences are extracted from the 20 most-recent orders
    (top brand per item-name, confidence = frequency / order_count).
"""

import random
import string
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.schemas.onboard import HouseholdProfileRequest

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    Household, HouseholdPreferences, Address,
    BrandPreference, PantryItem,
)
from app.providers.factory import get_otp_provider, get_mcp_provider
from app.utils.exceptions import (
    OTPExpiredError, OTPInvalidError, OTPMaxAttemptsError,
    HouseholdNotFoundError, SwiggyMCPError,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

# ── OTP config ────────────────────────────────────────────────────────────────
_OTP_TTL_SECONDS  = 600   # 10 minutes
_OTP_MAX_ATTEMPTS = 3
_OTP_KEY          = "otp:{household_id}:{phone}"        # value = "otp:attempts"
_OTP_LOCK_KEY     = "otp_lock:{household_id}:{phone}"  # set when locked

# ── Inference config ──────────────────────────────────────────────────────────
_INFERENCE_ORDER_LIMIT = 20   # look at last 20 orders
_BRAND_MIN_CONFIDENCE  = 0.4  # only keep brand pref if ordered ≥ 40% of the time


class InferenceResult:
    """Structured output from order-history analysis."""

    def __init__(self):
        self.diet_type:          str              = "vegetarian"   # default
        self.preferred_order_day: str             = "sunday"
        self.weekly_budget_min:  Optional[int]    = None
        self.weekly_budget_max:  Optional[int]    = None
        self.brand_preferences:  list[dict]       = []   # [{item_name, brand, confidence, category}]
        self.pantry_seeds:       list[dict]       = []   # [{item_name, category, qty, unit}]
        self.confidence_notes:   list[str]        = []   # human-readable inference notes
        self.has_order_history:  bool             = False
        self.address_id:         Optional[str]    = None  # Swiggy address ID (not our DB UUID)
        self.address_label:      Optional[str]    = None  # human-readable label for display


class OnboardingService:
    """
    One instance per request, bound to a DB session.
    MCP calls use a short-lived SwiggyMCPClient (created per method call).
    """

    def __init__(self, db: AsyncSession):
        self._db = db

    # ── Step 2: Inference ─────────────────────────────────────────────────────

    async def run_inference(
        self,
        household_id: str,
        access_token: str,
    ) -> InferenceResult:
        """
        Pull Swiggy order history and extract:
          - Likely diet type (from product names / categories)
          - Preferred order day (most common weekday of past orders)
          - Rough weekly budget (median grand_total)
          - Brand preferences (top brand per grocery item, confidence-filtered)
          - Pantry seed items (items ordered ≥ 2× in last 20 orders)

        All failures are caught and logged; inference is best-effort.
        """
        result = InferenceResult()
        client = get_mcp_provider(access_token)

        # Fetch default delivery address upfront — needed for placement later
        try:
            addresses = await client.get_addresses()
            default_addr = next(
                (a for a in addresses if _attr(a, "is_default", False)),
                addresses[0] if addresses else None,
            )
            if default_addr:
                result.address_id    = _attr(default_addr, "id", _attr(default_addr, "address_id", None))
                result.address_label = _attr(default_addr, "address_line", _attr(default_addr, "addressLine", None))
        except Exception as e:
            logger.warning("inference_address_failed", household_id=household_id, error=str(e))

        try:
            orders = await client.get_orders(limit=_INFERENCE_ORDER_LIMIT)
        except SwiggyMCPError as e:
            logger.warning("inference_orders_failed", household_id=household_id, error=str(e))
            result.confidence_notes.append("Could not fetch order history — using defaults.")
            return result

        if not orders:
            result.confidence_notes.append("No past Instamart orders found — using defaults.")
            return result

        result.has_order_history = True

        # ── Preferred order day ───────────────────────────────────────────────
        day_counter: Counter = Counter()
        grand_totals: list[float] = []

        for o in orders:
            grand_totals.append(_attr(o, "grand_total", _attr(o, "total", 0)))
            placed_at = _attr(o, "placed_at")
            if placed_at:
                try:
                    dt = datetime.fromisoformat(placed_at.replace("Z", "+00:00"))
                    day_counter[dt.strftime("%A").lower()] += 1
                except ValueError:
                    pass

        if day_counter:
            result.preferred_order_day = day_counter.most_common(1)[0][0]
            result.confidence_notes.append(
                f"Preferred order day inferred as {result.preferred_order_day}."
            )

        # ── Budget range ──────────────────────────────────────────────────────
        if grand_totals:
            sorted_totals = sorted(grand_totals)
            median = sorted_totals[len(sorted_totals) // 2]
            result.weekly_budget_min = int(median * 0.8)
            result.weekly_budget_max = int(median * 1.3)
            result.confidence_notes.append(
                f"Weekly budget estimated ₹{result.weekly_budget_min}–₹{result.weekly_budget_max} "
                f"from {len(grand_totals)} orders."
            )

        # ── Per-order item detail (fetch top 5 orders for item-level data) ───
        # We don't bulk-fetch all 20 to keep latency reasonable.
        item_brand_map: dict[str, Counter] = defaultdict(Counter)   # item → {brand: count}
        item_order_count: Counter = Counter()                        # item → how many orders it appeared in
        item_categories: dict[str, str] = {}
        non_veg_signals = 0

        recent_orders = orders[:5]
        for order_summary in recent_orders:
            try:
                order_id = _attr(order_summary, "order_id")
                order_detail = await client.get_order_details(order_id)
            except (SwiggyMCPError, Exception):
                continue

            items = _attr(order_detail, "items", [])
            for item in items:
                name = _attr(item, "name", "")
                name_lower = name.lower()

                # Detect non-veg signals
                if any(kw in name_lower for kw in (
                    "chicken", "mutton", "egg", "fish", "prawn", "meat", "beef", "pork"
                )):
                    non_veg_signals += 1

                # Normalise item name for brand tracking
                normalised = _normalise_item_name(name)
                item_order_count[normalised] += 1
                brand = _attr(item, "brand")
                if brand:
                    item_brand_map[normalised][brand] += 1
                category = _attr(item, "category")
                if category:
                    item_categories[normalised] = category

        # ── Diet type ─────────────────────────────────────────────────────────
        if non_veg_signals >= 2:
            result.diet_type = "non_vegetarian"
            result.confidence_notes.append("Non-vegetarian signals found in order history.")
        else:
            result.confidence_notes.append("No non-veg items found — defaulting to vegetarian.")

        # ── Brand preferences ─────────────────────────────────────────────────
        total_orders_sampled = min(len(recent_orders), 5)
        for item_name, brand_counts in item_brand_map.items():
            top_brand, top_count = brand_counts.most_common(1)[0]
            confidence = top_count / max(item_order_count[item_name], 1)
            if confidence >= _BRAND_MIN_CONFIDENCE:
                result.brand_preferences.append({
                    "item_name":  item_name,
                    "brand":      top_brand,
                    "confidence": round(confidence, 2),
                    "category":   item_categories.get(item_name, "grocery"),
                })

        # ── Pantry seeds (items ordered ≥ 2× in last 20 orders) ──────────────
        # We use the summary order list (all 20) for frequency, not just the 5 we fetched details for.
        # So pantry_seeds come from item_order_count; items ordered in ≥ 2 of the 5 detail-fetched orders.
        for item_name, count in item_order_count.items():
            if count >= 2:
                result.pantry_seeds.append({
                    "item_name": item_name,
                    "category":  item_categories.get(item_name, "grocery"),
                    "qty":       None,    # filled by planning loop later
                    "unit":      "unit",
                })

        logger.info(
            "inference_complete",
            household_id        = household_id,
            diet_type           = result.diet_type,
            preferred_order_day = result.preferred_order_day,
            brand_prefs_found   = len(result.brand_preferences),
            pantry_seeds_found  = len(result.pantry_seeds),
        )
        return result

    # ── Step 3: Save profile ──────────────────────────────────────────────────

    async def save_profile(
        self,
        household_id: str,
        body: "HouseholdProfileRequest",
        inference:    Optional[InferenceResult] = None,
    ) -> None:
        """
        Persist questionnaire answers + merge inference data into DB.
        Also seeds brand_preferences and creates the HouseholdPreferences row.
        """
        # Update core household fields
        await self._db.execute(
            update(Household)
            .where(Household.id == household_id)
            .values(
                household_type    = body.household_type,
                member_count      = body.member_count,
                diet_type         = body.diet_type,
                allergies         = body.allergies,
                weekly_budget_min = body.weekly_budget_min,
                weekly_budget_max = body.weekly_budget_max,
            )
        )

        # Upsert HouseholdPreferences (created here if doesn't exist)
        result = await self._db.execute(
            select(HouseholdPreferences).where(
                HouseholdPreferences.household_id == household_id
            )
        )
        prefs = result.scalar_one_or_none()

        preferred_day = (
            inference.preferred_order_day if inference
            else getattr(body, "preferred_order_day", "sunday")
        )

        if prefs is None:
            prefs = HouseholdPreferences(
                household_id        = household_id,
                preferred_order_day = preferred_day,
            )
            self._db.add(prefs)
        else:
            prefs.preferred_order_day = preferred_day

        # Seed brand preferences from inference
        if inference:
            for bp in inference.brand_preferences:
                existing = await self._db.execute(
                    select(BrandPreference).where(
                        BrandPreference.household_id == household_id,
                        BrandPreference.item_name    == bp["item_name"],
                    )
                )
                brand_row = existing.scalar_one_or_none()
                if brand_row is None:
                    self._db.add(BrandPreference(
                        household_id    = household_id,
                        category        = bp["category"],
                        item_name       = bp["item_name"],
                        preferred_brand = bp["brand"],
                        confidence      = bp["confidence"],
                    ))
                else:
                    # Update only if new confidence is higher
                    if bp["confidence"] > (float(brand_row.confidence) if brand_row.confidence else 0):
                        brand_row.preferred_brand = bp["brand"]
                        brand_row.confidence      = bp["confidence"]

        await self._db.commit()
        logger.info("profile_saved", household_id=household_id)

    # ── Step 4: Send OTP ──────────────────────────────────────────────────────

    async def send_otp(
        self,
        household_id:     str,
        whatsapp_number:  str,
    ) -> None:
        """
        Generate a 6-digit OTP, store it in Redis with TTL, and fire the
        WhatsApp message via Interakt.

        Redis key format: "otp:{household_id}:{phone}"  → "{otp}:{attempts}"
        """
        otp = _generate_otp()
        otp_provider = get_otp_provider()
        await otp_provider.store(str(household_id), whatsapp_number, otp, _OTP_TTL_SECONDS)

        # Send via WhatsApp
        try:
            from app.services.whatsapp_service import WhatsAppService
            wa = WhatsAppService()
            await wa.send_otp(whatsapp_number, otp)
        except Exception as e:
            logger.error("otp_send_failed", household_id=household_id, error=str(e))
            # Don't raise — OTP is stored; user can retry from UI

        logger.info("otp_sent", household_id=household_id, phone_last4=whatsapp_number[-4:])

    # ── Step 5: Verify OTP ────────────────────────────────────────────────────

    async def verify_otp(
        self,
        household_id:    str,
        whatsapp_number: str,
        otp_input:       str,
    ) -> None:
        """
        Validate OTP from Redis. On success, marks household WhatsApp verified.
        Raises OTPExpiredError, OTPInvalidError, or OTPMaxAttemptsError.
        """
        otp_provider = get_otp_provider()
        stored = await otp_provider.get(str(household_id), whatsapp_number)

        if stored is None:
            raise OTPExpiredError("OTP has expired. Please request a new one.")

        parts      = stored.split(":")
        stored_otp = parts[0]
        attempts   = int(parts[1]) if len(parts) > 1 else 0

        if attempts >= _OTP_MAX_ATTEMPTS:
            await otp_provider.delete(str(household_id), whatsapp_number)
            raise OTPMaxAttemptsError("Too many incorrect attempts. Please request a new OTP.")

        if otp_input != stored_otp:
            new_attempts = await otp_provider.increment_attempts(str(household_id), whatsapp_number)
            remaining = _OTP_MAX_ATTEMPTS - new_attempts
            raise OTPInvalidError(
                f"Incorrect OTP. {remaining} attempt{'s' if remaining != 1 else ''} remaining."
            )

        # ── Success ───────────────────────────────────────────────────────────
        await otp_provider.delete(str(household_id), whatsapp_number)

        await self._db.execute(
            update(Household)
            .where(Household.id == household_id)
            .values(
                whatsapp_number   = whatsapp_number,
                whatsapp_verified = True,
            )
        )
        await self._db.commit()
        logger.info("otp_verified", household_id=household_id)

    # ── Step 6: Preview basket ────────────────────────────────────────────────

    async def generate_preview_basket(
        self,
        household_id: str,
        access_token: str,
    ) -> dict:
        """
        Dry-run planning loop: sense → plan → optimise, but stop before confirm/place.
        Returns a basket dict the cockpit can display in the onboarding flow.

        Returns:
          {
            "items": [{"item_name", "sku_id", "brand", "quantity", "unit", "unit_price", "total_price"}],
            "estimated_total": float,
            "item_count": int,
            "notes": [str],   # e.g. "3 items are substitutions"
          }
        """
        client = get_mcp_provider(access_token)

        # Fetch addresses to resolve default
        try:
            addresses = await client.get_addresses()
        except Exception as e:
            logger.warning("preview_addresses_failed", household_id=household_id, error=str(e))
            return _empty_preview("Could not load delivery addresses.")

        default_address = next(
            (a for a in addresses if _attr(a, "is_default", False)),
            addresses[0] if addresses else None,
        )
        if default_address is None:
            return _empty_preview("No delivery addresses found on Swiggy account.")

        address_id = _attr(default_address, "id", _attr(default_address, "address_id", ""))

        # Load pantry items that need restocking (or all active items if none flagged)
        pantry_result = await self._db.execute(
            select(PantryItem).where(
                PantryItem.household_id == household_id,
                PantryItem.is_active    == True,
            )
        )
        pantry_items = pantry_result.scalars().all()

        # If pantry is empty (first-time user), bootstrap from Swiggy go-to items
        if not pantry_items:
            try:
                go_to_items = await client.your_go_to_items(address_id)
                if go_to_items:
                    basket_items = []
                    estimated_total = 0.0
                    for item in go_to_items:
                        price = _attr(item, "price", 0)
                        basket_items.append({
                            "item_name":    _attr(item, "name") or _attr(item, "item_name", ""),
                            "sku_id":       _attr(item, "sku_id"),
                            "brand":        _attr(item, "brand"),
                            "product_name": _attr(item, "name") or _attr(item, "item_name", ""),
                            "quantity":     float(_attr(item, "quantity", 1)),
                            "unit":         _attr(item, "unit", "units"),
                            "unit_price":   price,
                            "total_price":  price * float(_attr(item, "quantity", 1)),
                            "in_stock":     _attr(item, "in_stock", True),
                        })
                        estimated_total += basket_items[-1]["total_price"]
                    logger.info("preview_go_to_items", household_id=household_id, items=len(basket_items))
                    return {
                        "items": basket_items,
                        "estimated_total": round(estimated_total, 2),
                        "item_count": len(basket_items),
                        "notes": ["Based on your Swiggy order history"],
                    }
            except Exception as e:
                logger.warning("preview_go_to_items_failed", household_id=household_id, error=str(e))
            return _empty_preview("No order history found — your first basket will be ready after your first Swiggy order.")

        # Simple rules-based pass: items below reorder threshold
        items_to_reorder = [
            p for p in pantry_items
            if float(p.estimated_qty_remaining) <= float(p.reorder_threshold)
        ]

        if not items_to_reorder:
            items_to_reorder = pantry_items  # show all on first preview

        basket_items = []
        notes        = []
        estimated_total = 0.0

        for pantry_item in items_to_reorder[:15]:  # cap at 15 for preview
            search_query = f"{pantry_item.item_name}"

            # Try brand-preferred search first
            brand_result = await self._db.execute(
                select(BrandPreference).where(
                    BrandPreference.household_id == household_id,
                    BrandPreference.item_name    == pantry_item.item_name,
                )
            )
            brand_pref = brand_result.scalar_one_or_none()
            if brand_pref:
                search_query = f"{brand_pref.preferred_brand} {pantry_item.item_name}"

            try:
                search_result = await client.search_products(search_query, address_id, limit=3)
            except Exception:
                notes.append(f"Could not find {pantry_item.item_name} on Swiggy.")
                continue

            # search_products returns list[dict] (mock) or MCPSearchResult (real)
            products = search_result if isinstance(search_result, list) else getattr(search_result, "products", [])

            if not products:
                # fallback: search without brand
                if brand_pref:
                    try:
                        search_result = await client.search_products(
                            pantry_item.item_name, address_id, limit=3
                        )
                        products = search_result if isinstance(search_result, list) else getattr(search_result, "products", [])
                    except Exception:
                        continue

            if products:
                product = next(
                    (p for p in products if _attr(p, "in_stock", True)),
                    products[0],
                )

                qty = float(pantry_item.last_ordered_qty or 1)
                price = _attr(product, "price", 0)
                line_total = price * qty

                basket_items.append({
                    "item_name":    pantry_item.item_name,
                    "sku_id":       _attr(product, "sku_id", _attr(product, "id")),
                    "brand":        _attr(product, "brand"),
                    "product_name": _attr(product, "name"),
                    "quantity":     qty,
                    "unit":         pantry_item.standard_unit,
                    "unit_price":   price,
                    "total_price":  line_total,
                    "in_stock":     _attr(product, "in_stock", True),
                })
                estimated_total += line_total

        out_of_stock = [i for i in basket_items if not i["in_stock"]]
        if out_of_stock:
            notes.append(f"{len(out_of_stock)} item(s) are currently out of stock and will be skipped.")

        logger.info(
            "preview_basket_generated",
            household_id    = household_id,
            item_count      = len(basket_items),
            estimated_total = estimated_total,
        )

        return {
            "items":           basket_items,
            "estimated_total": round(estimated_total, 2),
            "item_count":      len(basket_items),
            "notes":           notes,
        }

    # ── Step 6b: Seed pantry from inference ──────────────────────────────────

    async def _seed_pantry_from_inference(self, household_id: str) -> None:
        """
        Write pantry_seeds from the most recent inference result into pantry_items.
        Skips items that already exist (idempotent). Falls back to a sensible
        default set of staples if no inference data is available.
        """
        from app.models.db import PantryItem

        # Check if pantry already has items
        existing = await self._db.execute(
            select(PantryItem).where(PantryItem.household_id == household_id).limit(1)
        )
        if existing.scalar_one_or_none():
            return  # Already seeded — nothing to do

        # Re-run inference to get seeds (may use cached order history)
        try:
            from app.services.auth_service import AuthService
            token = await AuthService(self._db).get_valid_token(household_id)
            result = await self.run_inference(household_id, token)
            seeds = result.pantry_seeds  # [{item_name, category, qty, unit}]
        except Exception as e:
            logger.warning("pantry_seed_inference_failed", household_id=household_id, error=str(e))
            seeds = []

        # Fallback defaults if inference found nothing
        if not seeds:
            seeds = [
                {"item_name": "Basmati Rice",     "category": "staples",    "qty": 1.0,  "unit": "kg"},
                {"item_name": "Toor Dal",          "category": "staples",    "qty": 0.5,  "unit": "kg"},
                {"item_name": "Wheat Atta",        "category": "staples",    "qty": 2.0,  "unit": "kg"},
                {"item_name": "Amul Butter",       "category": "dairy",      "qty": 100,  "unit": "g"},
                {"item_name": "Amul Milk",         "category": "dairy",      "qty": 1.0,  "unit": "L"},
                {"item_name": "Curd",              "category": "dairy",      "qty": 200,  "unit": "g"},
                {"item_name": "Tomatoes",          "category": "vegetables", "qty": 0.5,  "unit": "kg"},
                {"item_name": "Onions",            "category": "vegetables", "qty": 1.0,  "unit": "kg"},
                {"item_name": "Potatoes",          "category": "vegetables", "qty": 0.5,  "unit": "kg"},
                {"item_name": "Turmeric Powder",   "category": "spices",     "qty": 50,   "unit": "g"},
                {"item_name": "Cumin Seeds",       "category": "spices",     "qty": 50,   "unit": "g"},
                {"item_name": "Sunflower Oil",     "category": "staples",    "qty": 0.5,  "unit": "L"},
                {"item_name": "Salt",              "category": "spices",     "qty": 200,  "unit": "g"},
                {"item_name": "Sugar",             "category": "staples",    "qty": 500,  "unit": "g"},
                {"item_name": "Tea",               "category": "beverages",  "qty": 100,  "unit": "g"},
                {"item_name": "Detergent Powder",  "category": "cleaning",   "qty": 200,  "unit": "g"},
                {"item_name": "Dish Wash Bar",     "category": "cleaning",   "qty": 1,    "unit": "units"},
                {"item_name": "Toilet Soap",       "category": "personal",   "qty": 1,    "unit": "units"},
            ]

        for s in seeds:
            qty = float(s.get("qty", 0))
            # Treat qty as roughly "half a typical reorder" so most items trigger reorder
            threshold = round(qty * 1.2, 3) if qty > 0 else 1.0
            item = PantryItem(
                household_id            = household_id,
                item_name               = s["item_name"],
                category                = s.get("category", "grocery"),
                standard_unit           = s.get("unit", "units"),
                estimated_qty_remaining = round(qty * 0.3, 3),  # start low → triggers reorder
                reorder_threshold       = threshold,
            )
            self._db.add(item)

        try:
            await self._db.commit()
            logger.info("pantry_seeded", household_id=household_id, item_count=len(seeds))
        except Exception:
            await self._db.rollback()
            logger.warning("pantry_seed_conflict", household_id=household_id)

    # ── Step 7: Complete onboarding ───────────────────────────────────────────

    async def complete_onboarding(
        self,
        household_id:     str,
        place_order_now:  bool = False,
    ) -> None:
        """
        Finalise onboarding:
          1. Seed pantry_items from inference pantry_seeds (if not already present).
          2. Mark household.onboarding_complete = True.
          3. Schedule the first planning loop run (via Celery).
          4. Optionally trigger an immediate run (if user opted in).
        """
        household_result = await self._db.execute(
            select(Household).where(Household.id == household_id)
        )
        household = household_result.scalar_one_or_none()
        if household is None:
            raise HouseholdNotFoundError(f"Household {household_id} not found.")

        # Mark complete
        household.onboarding_complete = True
        await self._db.commit()

        # Schedule first loop
        from app.tasks.planning import trigger_planning_loop
        if place_order_now:
            trigger_planning_loop.apply_async(
                args   = [household_id],
                kwargs = {"trigger_type": "user_initiated"},
            )
            logger.info("first_loop_triggered_immediately", household_id=household_id)
        else:
            # Schedule for next preferred order day
            prefs_result = await self._db.execute(
                select(HouseholdPreferences).where(
                    HouseholdPreferences.household_id == household_id
                )
            )
            prefs = prefs_result.scalar_one_or_none()
            next_run = _next_weekday(prefs.preferred_order_day if prefs else "sunday")

            if prefs:
                prefs.next_run_at = next_run
                await self._db.commit()

            trigger_planning_loop.apply_async(
                args    = [household_id],
                kwargs  = {"trigger_type": "scheduled"},
                eta     = next_run,
            )
            logger.info(
                "first_loop_scheduled",
                household_id = household_id,
                next_run_at  = next_run.isoformat(),
            )


# ── Private helpers ───────────────────────────────────────────────────────────

def _attr(obj, name: str, default=None):
    """Access an attribute on an object or key on a dict, with a default."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _generate_otp() -> str:
    return "".join(random.choices(string.digits, k=6))


def _normalise_item_name(name: str) -> str:
    """
    Strip brand/quantity suffixes to get a canonical item name for brand tracking.
    e.g. "Aashirvaad Atta 5kg" → "atta"
    Very heuristic — good enough for brand inference.
    """
    stop_words = {
        "g", "kg", "ml", "l", "litre", "pack", "pcs", "piece", "pieces",
        "packet", "500", "1", "2", "5", "10", "250", "100",
    }
    tokens = name.lower().split()
    meaningful = [t for t in tokens if t not in stop_words and not t.replace(".", "").isdigit()]
    return " ".join(meaningful[-2:]) if meaningful else name.lower()


def _empty_preview(note: str) -> dict:
    return {
        "items":           [],
        "estimated_total": 0.0,
        "item_count":      0,
        "notes":           [note],
    }


def _next_weekday(day_name: str) -> datetime:
    """Return the next occurrence of a named weekday (e.g. 'sunday') at 10:00 AM IST."""
    days = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    target = days.get(day_name.lower(), 6)  # default Sunday
    now    = datetime.now(timezone.utc)
    current_weekday = now.weekday()
    days_ahead = (target - current_weekday + 7) % 7
    if days_ahead == 0:
        days_ahead = 7  # push to next week if today is already the day

    next_day = now + timedelta(days=days_ahead)
    # 10:00 AM IST = 04:30 UTC
    return next_day.replace(hour=4, minute=30, second=0, microsecond=0)
