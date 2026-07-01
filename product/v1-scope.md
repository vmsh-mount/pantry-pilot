# PantryPilot — V1 Scope
*Last updated: 2026-06-26*
*Status: Draft — reviewed*

---

## What V1 Is

A closed beta for 50–100 households in Bengaluru. The goal is not scale — it's to prove the core loop works and that households trust it enough to let it plan their groceries.

V1 is the **Smart Planner** phase of Direction C. Nutrition intelligence exists in the background but the user's first experience is purely about reducing planning overhead.

---

## What V1 Is Not

- Not a mobile app
- Not a fully autonomous agent (user always confirms before order placement)
- Not multi-city
- Not multi-platform (Swiggy Instamart only)
- Not open to the public (invite-only closed beta)
- Not a nutrition tracker, medical app, or calorie logger
- Not a recipe planner (V3)

---

## Platform Decisions

| Decision | Choice | Reason |
|---|---|---|
| Sign-up & onboarding | Minimal web app (pantrypilot.in) | Clean OAuth flow, trust signal |
| Basket confirmation | WhatsApp (via Interakt BSP) | Zero friction, lives where users already are |
| Order placement | Swiggy Instamart MCP | Only available fulfilment rail in V1 |
| Backend | FastAPI (Python) | LangGraph compatibility, fast to build |
| LLM | Claude (via Anthropic API) | Planning loop intelligent layer |
| Agent framework | LangGraph | Orchestrates Sense → Plan → Optimize loop |
| Hosting | AWS ap-south-1 (Mumbai) | Proximity to Swiggy infra, DPDP compliance |
| Database | Postgres + Redis | Household profiles, pantry state, session cache |

---

## Features In Scope (V1)

### 1. Authentication
Swiggy OAuth 2.1 + PKCE. User connects Swiggy account via web app. Token stored encrypted server-side. Proactive re-auth reminders via WhatsApp before 5-day token expires. No refresh tokens in Swiggy v1.0 — full re-auth required every 5 days.

→ See [docs/auth.md](docs/auth.md)

---

### 2. Onboarding
3-question visual questionnaire (household size, diet type, budget) + Swiggy order history inference. User sees what we already know about them before being asked anything else. Ends with a first basket preview and WhatsApp number linking via OTP.

→ See [docs/onboarding.md](docs/onboarding.md)

---

### 3. Planning Loop
Sense → Plan → Optimize → Confirm → Place. Rules-first basket skeleton, LLM intelligent layer on top. Triggered on weekly schedule. Sends basket card to WhatsApp — user has a 4-hour confirmation window. Order placed via Swiggy MCP on confirmation. Timeout = skip, never auto-order in V1.

→ See [docs/planning-loop.md](docs/planning-loop.md)

---

### 4. WhatsApp Integration
Provider: Interakt (Meta BSP). 6 pre-approved message templates: OTP, basket preview, order receipt, re-auth reminders (48hr + 24hr), session expired. Interactive button-based confirmation. Inbound message handling for edits, skips, and pauses.

→ See [docs/whatsapp-integration.md](docs/whatsapp-integration.md)

---

### 5. Pantry State
Inferred from Swiggy order history — no manual logging. Bootstrap from 6 months of `get_orders` history. Passive consumption decay between orders. Updated post-order via `get_order_details`. Consumption rates learn from user edit behaviour over time.

→ See [docs/pantry-state.md](docs/pantry-state.md)

---

### 6. Order Frequency

**V1 default:** Single weekly schedule per household. User picks a preferred day and time during onboarding (e.g. Sunday 10 AM).

**Data model:** Built from day one to support per-category frequency — V1.5 unlocks it without a schema rewrite.

| Category Bucket | V1 Default | V1.5 User-Configurable |
|---|---|---|
| Staples (dal, rice, oil, atta) | Weekly | Weekly / Fortnightly |
| Fresh produce (vegetables, fruits) | Weekly | Every 2–3 days |
| Dairy & eggs | Weekly | Daily / Every 2–3 days |
| Packaged & snacks | Weekly | Weekly / Fortnightly |

**Why not expose frequency controls in V1:** Reduces onboarding complexity, keeps the loop simpler to debug, and weekly is a reasonable default for a closed beta.

---

## Features Explicitly Out of Scope (V1)

| Feature | Version | Notes |
|---|---|---|
| Nutrition scoring (NFI) | V2 | After planning loop is stable |
| Confidence Score / auto-confirm | V2 | Requires trust data from V1 |
| Per-category order frequency | V1.5 | Data model supports it already |
| Non-vegetarian household support | V1.5 | Vegetarian-first in closed beta |
| Festival / fasting calendar | V1.5 | — |
| Daily standing orders (e.g. milk) | V1.5 | Extension Pattern 1 — see future-extensions.md |
| Filter-driven discovery (healthy snacks) | V2 | Depends on SKU-level ingredient data from Swiggy |
| Recipe-aware planning (biryani basket) | V3 | Extension Pattern 2 — needs recipe DB + ingredient-SKU mapping |
| Multi-platform (Zepto, BigBasket) | V3+ | Architecture is platform-agnostic by design |
| Voice onboarding | V3+ | — |
| Mobile app | Post-beta | Only if closed beta demand warrants it |

→ See [future-extensions.md](future-extensions.md) for elaborated extension plans

---

## V1 Success Metrics

| Metric | Target by Month 3 |
|---|---|
| Households in closed beta | 50–100 |
| Households placing ≥ 1 order via PantryPilot | ≥ 80% |
| Weekly basket acceptance rate (confirmed without edits) | ≥ 50% |
| Average orders per household per month | 3.5+ |
| Re-auth drop-off rate | < 20% |
| NPS | ≥ 45 |

---

## Open Questions

- [ ] What is Swiggy MCP's rate limit per household per day? Critical for planning loop design (15+ `search_products` calls per basket).
- [ ] Does `search_products` return SKU-level ingredient or nutritional data? Needed for V2 filter-driven discovery.
- [ ] Does Swiggy v1.1 refresh token have an ETA? Affects re-auth UX significantly.
- [ ] Is there a Swiggy sandbox with test accounts for development?
- [ ] Which Interakt plan supports interactive button messages? Confirm before signing up.
- [ ] Bootstrap accuracy: should we ask one optional "pantry check" question during onboarding to improve day-1 estimates?

---

## Document Index

| Document | Purpose | Status |
|---|---|---|
| [design/high-level-design.md](design/high-level-design.md) | System architecture, sequence diagrams, infra | ✅ Complete |
| [design/low-level-design.md](design/low-level-design.md) | DB schema, API contracts, service interfaces, Celery tasks | ✅ Complete |
| [mission-vision.md](mission-vision.md) | Who we are, what we're building towards | ✅ Complete |
| [re-ideation.md](re-ideation.md) | Product direction rationale (Direction C) | ✅ Complete |
| [future-extensions.md](future-extensions.md) | Elaborated future use cases and extension patterns | ✅ Complete |
| [docs/auth.md](docs/auth.md) | Swiggy OAuth flow, token handling, re-auth strategy | ✅ Complete |
| [docs/onboarding.md](docs/onboarding.md) | Sign-up, questionnaire, inference, first basket | ✅ Complete |
| [docs/planning-loop.md](docs/planning-loop.md) | Sense → Plan → Optimize → Confirm → Place | ✅ Complete |
| [docs/whatsapp-integration.md](docs/whatsapp-integration.md) | Provider, templates, conversation flows | ✅ Complete |
| [docs/pantry-state.md](docs/pantry-state.md) | Inventory tracking, decay, consumption learning | ✅ Complete |
| [docs/nutrition-engine.md](docs/nutrition-engine.md) | NFI scoring, ICMR-NIN RDAs, basket optimisation | 🔲 V2 |
| [docs/confidence-score.md](docs/confidence-score.md) | Trust gating, auto-confirm levels | 🔲 V2 |
