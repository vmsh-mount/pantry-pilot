# PantryPilot — Future Extensions
*Last updated: 2026-06-26*

---

## Overview

This document tracks use cases and feature ideas that are out of scope for V1 but are explicitly designed for. Every extension here has been evaluated against the V1 architecture to ensure it can be added without a rewrite.

Extensions are grouped by the pattern they represent — not by version, because sequencing will depend on what users actually ask for in the beta.

---

## Extension Pattern 1: Frequency Extension
*"I want to order this item more often than weekly"*

### Use Case: Daily Milk Order with Preferred Brand

**User story:**
> User A wants fresh milk delivered every morning — always Amul Taaza, always 1 litre.

**What's needed:**
- Per-category schedule trigger (one `next_run_at` per category bucket per household)
- A lightweight "quick order" flow — no full Sense → Plan → Optimize loop needed for a single recurring item. Just: check stock → confirm → place.
- Brand pinning at the item level (already partially supported via `brand_preference` in `pantry_items`)
- A "standing order" concept: an item + quantity + frequency + brand that runs on its own cadence independent of the weekly basket

**Architecture fit:**
- `pantry_items` table already has `category` field mapped to category buckets
- Order frequency data model was designed from day one to support per-category scheduling (see v1-scope.md — Order Frequency section)
- Quick order flow is a simplified subset of the existing planning loop — reuses OPTIMIZE + CONFIRM + PLACE stages, skips SENSE + PLAN

**What needs to be built:**
- [ ] Per-category `next_run_at` in `household_preferences` table
- [ ] Standing order record: `item_id`, `qty`, `frequency`, `brand`, `active`
- [ ] Lightweight quick-order loop (no LLM, no rules engine — just resolve SKU, confirm, place)
- [ ] WhatsApp quick-confirm template: "Your 1L Amul Taaza is ready to order — ₹28. Confirm?" [ ✅ Yes ] [ ❌ Skip today ]

**Estimated extension effort:** Low — data model already supports it, loop is a subset of existing flow.

**Version target:** V1.5

---

## Extension Pattern 2: Intent-Based Ordering
*"I want to cook something specific — build me a basket for it"*

### Use Case: Recipe-Driven Basket (e.g. Biryani)

**User story:**
> User B messages: "I'm making chicken biryani for 6 people on Saturday."
> PantryPilot responds with the ingredient list, cross-checks pantry state, and generates a basket for only what's missing.

**What's needed:**
- A recipe database: recipes → ingredients → quantities (scaled by servings)
- Ingredient → Instamart SKU mapping (e.g. "basmati rice" → specific SKU on Instamart)
- Cross-check against current pantry state (don't order jeera if they already have 50g)
- A new planning trigger: **user message / intent** rather than a schedule
- A one-time basket — not added to the recurring loop
- NLP to parse the intent from a WhatsApp message ("making biryani Saturday for 6")

**Architecture fit:**
- OPTIMIZE + CONFIRM + PLACE stages reused as-is
- New INTENT PARSE stage sits before SENSE
- SENSE stage already reads pantry state — pantry cross-check is a natural extension
- WhatsApp inbound message handler already exists — new intent type added to routing logic

**What needs to be built:**
- [ ] Recipe database (curated Indian recipes with ingredients + quantities)
- [ ] Ingredient → SKU mapping layer (generic ingredient name → Instamart search query)
- [ ] Serving size scaler (recipe for 4 → scale to 6)
- [ ] Intent parser: LLM extracts dish name, servings, date from free-text message
- [ ] Pantry cross-check: subtract what household already has from ingredient list
- [ ] One-time basket flow with clear labelling ("This is for your biryani — not your weekly order")

**New WhatsApp interaction:**
```
User: "Making biryani Saturday for 6"

PantryPilot: "Got it! Here's what you'll need for biryani (6 servings) 🍛

You already have: Jeera, Bay leaves, Curd
Still needed:
  Basmati Rice 1kg          ₹180
  Chicken 1kg               ₹380  (V1.5 — non-veg support)
  Saffron 1g                ₹85
  + 6 more items

Total: ₹890

[ 🛒 Add to this week's basket ]
[ 📦 Order separately ]
[ ✏️ Edit list ]"
```

**Estimated extension effort:** Medium-High — recipe DB and ingredient-SKU mapping are new infrastructure. Loop reuse is high.

**Version target:** V3

**Dependencies:**
- Non-veg household support (V1.5) for full recipe coverage
- Pantry state maturity — cross-check only works well after 6+ weeks of consumption tracking

---

## Extension Pattern 3: Filter-Driven Discovery
*"I want items matching specific health or ingredient criteria"*

### Use Case: Healthy Snack Basket (No Sugar, No Palm Oil)

**User story:**
> User C says: "Suggest healthy snacks — no added sugar, no palm oil."
> PantryPilot surfaces a curated snack basket that matches those constraints.

**What's needed:**
- SKU-level ingredient and nutritional data (does Swiggy expose this via `search_products`? TBD — needs validation)
- A filter engine that translates natural language constraints ("no palm oil") into catalogue exclusions
- A discovery mode: user is not replenishing something they already buy — they want to explore
- A new basket type: **category-specific, filter-driven** rather than household replenishment

**Architecture fit:**
- `search_products` is the entry point — but the depth of nutritional/ingredient data it returns is unknown
- If Swiggy exposes ingredient lists per SKU → filter engine sits on top of search results
- If Swiggy does NOT expose ingredient data → we need our own nutrition + ingredient DB mapped to Instamart SKUs (significant effort)
- CONFIRM + PLACE stages reused as-is

**What needs to be built:**
- [ ] Validate what `search_products` returns at SKU level — does it include ingredients, nutritional info?
- [ ] If yes: filter engine — parse constraints → apply as post-search filter on SKU results
- [ ] If no: curated SKU-level nutrition DB mapped to Instamart product IDs (ongoing maintenance burden)
- [ ] Constraint parser: LLM translates "no sugar, no palm oil" → structured filter rules
- [ ] Discovery basket flow: different from replenishment — framed as "here are some options" not "here's what you need"
- [ ] User feedback loop: thumbs up/down on suggestions to improve future discovery

**Known risk:**
SKU-level ingredient data is the critical dependency. If Swiggy MCP doesn't expose it, this feature requires maintaining our own nutrition database — an ongoing operational cost. Validate this before prioritising.

**Estimated extension effort:** Medium (if Swiggy exposes ingredient data) / High (if we build our own DB).

**Version target:** V2 (nutrition engine phase)

**Dependencies:**
- Nutrition engine (V2) — NFI scoring and nutritional awareness infrastructure overlaps significantly with this use case
- Validation of `search_products` response payload depth

---

## Cross-Cutting Themes

Looking across all three use cases, two themes emerge that should influence V2 architecture:

### Theme 1: Multiple Basket Types

V1 has one basket type: the weekly replenishment basket. Future extensions need:

| Basket Type | Trigger | Loop Used |
|---|---|---|
| Weekly replenishment | Schedule | Full Sense → Plan → Optimize → Confirm → Place |
| Standing order | Per-item schedule | Lightweight: Optimize → Confirm → Place |
| Recipe basket | User intent | Intent Parse → Sense (pantry cross-check) → Optimize → Confirm → Place |
| Discovery basket | User preference | Filter → Optimize → Confirm → Place |

The OPTIMIZE → CONFIRM → PLACE chain is common to all. Design it as a reusable pipeline component.

### Theme 2: Richer SKU Data

Use Cases 2 and 3 both depend on richer data per SKU than `search_products` may return today:
- Recipe basket needs ingredient → SKU mapping
- Healthy snack basket needs ingredient lists per SKU

Before investing in either feature, validate what `search_products` actually returns and whether Swiggy plans to enrich SKU data in their MCP roadmap. Raise with builders@swiggy.in.

---

## Ideas Parking Lot

Lower-fidelity ideas noted for future consideration — not yet elaborated:

- **Festival & fasting calendar** — auto-adjust basket for Navratri, Ekadashi, Ramzan, etc. (V1.5)
- **Guest mode** — "having 8 people over Sunday, scale up this week's order"
- **Budget month** — "tight this month, suggest a ₹1,200 basket" (one-time budget override)
- **Sharing a basket** — couple where both partners can approve/edit the basket
- **Dietitian co-pilot** — opt-in NFI report sharing with a registered dietitian (V3+)
- **Voice onboarding** — for non-app-native users: parents, elderly (V3+)
- **Multi-platform fulfilment** — Zepto, BigBasket, Blinkit as additional fulfilment rails (V3+)
