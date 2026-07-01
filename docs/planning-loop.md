# PantryPilot — Planning Loop
*Last updated: 2026-06-26*

---

## Overview

The planning loop is the core of PantryPilot. It runs on a per-household schedule and produces a suggested grocery basket that the user reviews and confirms via WhatsApp.

The loop has five stages:

```
SENSE → PLAN → OPTIMIZE → CONFIRM → PLACE
```

Each stage is discrete, logged, and independently debuggable. The loop is not a single LLM call — it is a structured pipeline where rules handle the predictable parts and the LLM handles the intelligent layer on top.

---

## Architecture Philosophy

**Rules-first. LLM on top.**

| Layer | Who handles it | What it does |
|---|---|---|
| Basket skeleton | Deterministic rules | Reorder staples running low, apply diet/allergy filters, respect budget |
| Intelligent layer | LLM (Claude via LangGraph) | Substitutions, gap-filling, variety nudges, out-of-stock reasoning |
| Catalogue resolution | Swiggy Instamart MCP | Resolve items to real SKUs, check price and stock |
| Confirmation | User via WhatsApp | Always required in V1 — no auto-placement |
| Placement | Swiggy Instamart MCP | `update_cart` + `checkout` |

**Why rules-first:** The basket should be predictable and debuggable. If a household gets an unexpected item, the reason must be traceable to a specific rule or LLM decision — not a black box. Trust is built through consistency, not cleverness.

---

## Trigger

The loop is triggered by a **per-household scheduler** running in the background.

Each household has a `next_run_at` timestamp set during onboarding (e.g. every Sunday at 10 AM). When the scheduler fires:

1. Checks that the household has a valid Swiggy access token (not expired)
2. If token is valid → run the loop
3. If token is expired → skip loop, send re-auth WhatsApp alert, reschedule

The scheduler is a simple cron job in V1 (APScheduler or Celery Beat). Per-category frequency (V1.5) will extend this to multiple `next_run_at` timestamps per household — one per category bucket.

---

## Stage 1: SENSE

**What:** Gather everything the loop needs to make decisions.

**Inputs collected:**

| Input | Source | How |
|---|---|---|
| Household profile | Postgres | households table — diet type, budget, member count, allergies |
| Pantry state | Postgres | pantry_items table — items, estimated quantities, last ordered date |
| Order history (recent) | Postgres + Swiggy MCP | Last 4 orders from `get_orders` — catch anything missed locally |
| Preferred delivery address | Postgres | address_id stored from onboarding |
| Delivery slot preference | Postgres | household_preferences table |
| Week context | Computed | Day of week, any flagged events (V1.5: festival calendar) |

**Output:** A `HouseholdContext` object passed to the Plan stage.

```python
HouseholdContext(
    household_id="abc123",
    member_count=2,
    diet_type="vegetarian",
    allergies=[],
    weekly_budget=2200,
    pantry_items=[
        PantryItem(name="Toor Dal", estimated_qty_remaining=0.2, unit="kg", reorder_threshold=0.5),
        PantryItem(name="Aashirvaad Atta", estimated_qty_remaining=1.5, unit="kg", reorder_threshold=1.0),
        ...
    ],
    brand_preferences={"dal": "Tata Sampann", "atta": "Aashirvaad"},
    order_history=[...],  # last 4 orders
    preferred_address_id="swiggy_addr_xyz",
    preferred_delivery_slot="evening"
)
```

---

## Stage 2: PLAN

**What:** Decide what the household needs this week. Produce a candidate basket before any catalogue resolution.

This stage runs in two sub-layers:

### Sub-layer A: Rules Engine (Basket Skeleton)

Deterministic logic. Fast. Produces the base candidate list.

**Rule 1 — Reorder depleted staples:**
For each item in `pantry_items` where `estimated_qty_remaining < reorder_threshold`:
→ Add to candidate basket at standard reorder quantity

**Rule 2 — Apply diet filter:**
Filter entire candidate basket against `diet_type` and `allergies`
→ Hard-block any item that violates dietary constraints — no exceptions

**Rule 3 — Budget guard:**
Estimate candidate basket cost using last known prices from order history
→ If over budget: flag items for LLM to deprioritise (cheapest substitution wins)
→ If significantly under budget: flag headroom for LLM to suggest additions

**Rule 4 — Frequency guard:**
Do not suggest an item ordered in the last 3 days (prevents double-ordering)
Check `get_orders` recency before adding any item

**Rule 5 — Minimum basket size:**
If candidate basket has fewer than 8 items, flag as thin → LLM fills gaps

**Output of Sub-layer A:** A `CandidateBasket` — a list of items with quantities, estimated costs, and flags.

---

### Sub-layer B: LLM Intelligent Layer (Claude via LangGraph)

The LLM receives the `CandidateBasket` + `HouseholdContext` and is asked to:

1. **Fill gaps** — if the basket is thin or a category is missing (e.g. no vegetables), suggest additions
2. **Add variety** — if the household has ordered the same 12 items for 4 weeks in a row, suggest a reasonable variation (same category, similar price)
3. **Apply seasonal awareness** — prefer in-season produce (June = mangoes, monsoon vegetables)
4. **Respect brand preferences** — use `brand_preferences` to pick the right variant when multiple exist
5. **Rationalise quantities** — adjust quantities based on household size and consumption patterns from order history

**LLM prompt structure (simplified):**

```
You are a household grocery planner for an Indian vegetarian household of 2 people 
with a weekly budget of ₹2,200.

Here is the candidate basket generated by the rules engine:
<candidate_basket>...</candidate_basket>

Here is the household's order history for context:
<order_history>...</order_history>

Your tasks:
1. Review the basket for gaps — are any essential categories missing?
2. Suggest up to 3 additions if budget allows
3. Flag any items that seem unusual or inconsistent with this household's patterns
4. Suggest variety where the household has been buying the same thing for 4+ weeks
5. Note seasonal produce available in June that would suit this household

Return a revised basket as structured JSON. Do not hallucinate product names — 
use generic category descriptions (e.g. "500g fresh spinach") that will be 
resolved against the Instamart catalogue in the next step.
Output must include a reason field for every addition or change.
```

**Output of Sub-layer B:** A `RevisedCandidateBasket` with reasons for every item.

---

## Stage 3: OPTIMIZE

**What:** Resolve the candidate basket against the live Instamart catalogue. Check real prices, stock, and find the best SKU match for each item.

This stage makes the actual MCP calls.

### Step 3.1 — SKU Resolution

For each item in `RevisedCandidateBasket`:

```python
results = search_products(
    query="Toor Dal 1kg",
    address_id="swiggy_addr_xyz"
)
```

Pick the best match using this priority order:
1. Exact brand match (if household has a brand preference for this category)
2. Closest quantity match to what was requested
3. Best price per unit
4. In-stock status (never add an out-of-stock item to the basket)

### Step 3.2 — Out-of-Stock Handling

If the best match for an item is out of stock:

**Find a substitute:**
- Search for the next best option in the same category
- Apply diet/allergy filter to the substitute
- If a valid substitute is found → add to basket, flag as substitution with reason

**Flag for user:**
Every substitution is surfaced explicitly in the basket card:
```
⚠️  Tata Sampann Toor Dal unavailable
    → Substituted with Fortune Toor Dal 1kg (₹148)
```

**If no substitute found:**
- Remove item from basket
- Flag as "unavailable this week" in basket card
- Note the gap for the LLM to potentially fill with an alternative category item

### Step 3.3 — Budget Reconciliation

After SKU resolution, sum the real prices:

- If total > budget: remove lowest-priority items (snacks and extras first, staples last) until within budget
- If total < budget by > ₹200: flag headroom, optionally add one suggested item (LLM picks)
- Final basket total locked in before Confirm stage

### Step 3.4 — Delivery Slot Check

```python
# Verify preferred delivery slot is available
# (Swiggy MCP handles slot availability at checkout)
```

In V1, we don't pre-check slots — we pass the preferred slot to `checkout` and handle conflicts at placement time.

**Output of Stage 3:** A `ResolvedBasket` — fully resolved SKUs, real prices, stock confirmed, substitutions flagged.

---

## Stage 4: CONFIRM

**What:** Send the basket to the user on WhatsApp and wait for their response.

→ Full interaction design in [docs/whatsapp-integration.md](whatsapp-integration.md)

### What the basket card contains:

```
Your grocery basket for this week 🛒

Aashirvaad Atta 5kg         ₹280
Tata Sampann Toor Dal 1kg   ₹145
Amul Butter 500g            ₹285
Fresh Tomatoes 1kg           ₹52
Spinach 500g                 ₹35
Onions 2kg                   ₹68
+ 7 more items

⚠️  1 substitution made (tap to see)
Total: ₹1,940 of ₹2,200 budget

[ ✅ Looks good, order it ]
[ ✏️ Let me review items  ]
[ ❌ Skip this week       ]
```

### Confirmation window: 4 hours

- Loop enters a waiting state after sending the basket card
- If user confirms → proceed to Place
- If user edits → apply edits, re-send updated card, restart 4-hour window
- If user skips → cancel cycle, schedule next
- If no response after 4 hours → auto-skip (never auto-order in V1), send gentle nudge at 4hr mark, final skip at 6hr

### Learning from edits

Every user edit is logged:
- Items removed
- Items added
- Quantity changes
- Time taken to respond

This data feeds back into the Plan stage over time — if the household removes spinach 3 weeks in a row, the rules engine stops suggesting it.

---

## Stage 5: PLACE

**What:** Place the confirmed basket as a Swiggy Instamart order.

### Step 5.1 — Clear and Build Cart

```python
# Clear any existing cart first
clear_cart()

# Add each confirmed item
update_cart(items=[
    {"product_id": "sku_123", "quantity": 1},
    {"product_id": "sku_456", "quantity": 2},
    ...
])

# Verify cart matches expected total
cart = get_cart()
assert cart.total == expected_total  # within ±5% tolerance for price changes
```

### Step 5.2 — Checkout

```python
checkout(
    address_id="swiggy_addr_xyz",
    delivery_slot="evening"  # user's preference
)
```

### Step 5.3 — Post-placement

On successful checkout:
1. Store `order_id` in Postgres linked to this household and loop run
2. Send Order Receipt via WhatsApp (Template 3)
3. Schedule a pantry state update (see [docs/pantry-state.md](pantry-state.md))
4. Log loop run as `completed` with full audit trail

### Step 5.4 — Placement Failures

| Failure | Cause | Action |
|---|---|---|
| 401 Unauthorized | Token expired | Pause loop, send re-auth alert |
| Item out of stock at checkout | Stock changed between Optimize and Place | Re-run Optimize stage for affected item, re-confirm with user if substitution needed |
| Cart total mismatch > 5% | Price spike between stages | Alert user with new total, ask re-confirmation before placing |
| Checkout timeout | Swiggy MCP unresponsive | Retry once after 60 seconds, alert user if second attempt fails |
| Payment failure | Swiggy-side issue | Alert user to check Swiggy app directly — outside our control |

---

## Full Loop Sequence Diagram

```
Scheduler fires (Sunday 10 AM)
        ↓
[SENSE] Fetch household context + pantry state
        ↓
[PLAN — Rules] Build basket skeleton
        ↓
[PLAN — LLM] Intelligent gap-fill + variety + seasonal
        ↓
[OPTIMIZE] Resolve SKUs via search_products
        ↓
[OPTIMIZE] Handle out-of-stock → substitute or flag
        ↓
[OPTIMIZE] Budget reconciliation
        ↓
[CONFIRM] Send basket card to WhatsApp
        ↓
        ├── User confirms (within 4hr) ──────────────────┐
        ├── User edits → re-confirm                      │
        ├── User skips → end loop, schedule next         │
        └── No response (6hr) → auto-skip, schedule next │
                                                         ↓
                                              [PLACE] clear_cart → update_cart
                                                         ↓
                                              [PLACE] checkout
                                                         ↓
                                              Send receipt → update pantry state
                                                         ↓
                                              Schedule next loop run
```

---

## Loop State Machine

Each loop run has a state tracked in Postgres:

| State | Meaning |
|---|---|
| `pending` | Scheduled, not yet started |
| `sensing` | Fetching household context |
| `planning` | Rules engine + LLM running |
| `optimizing` | MCP catalogue resolution in progress |
| `awaiting_confirmation` | Basket sent to WhatsApp, waiting for user |
| `placing` | Order being placed via MCP |
| `completed` | Order placed successfully |
| `skipped` | User skipped or timed out |
| `failed` | Error during loop — logged with reason |
| `paused` | Token expired or user paused automation |

---

## Logging & Observability

Every loop run is fully logged for debugging and learning:

- Loop run ID, household ID, trigger timestamp
- Each stage: start time, end time, input summary, output summary
- LLM calls: prompt (sanitised), response, tokens used, latency
- MCP calls: tool name, input, response summary, latency
- Substitutions made and reasons
- User response: action taken, time to respond, edits made
- Final order ID (if placed) or skip reason

**PII handling in logs:** WhatsApp numbers and item names logged, health data (dietary flags, allergies) never appear in logs — referenced by household ID only.

---

## Open Questions

- [ ] LLM model choice: Claude Sonnet (better reasoning, higher cost) vs Claude Haiku (faster, cheaper)? For a planning loop that runs once a week per household, Sonnet is justified.
- [ ] How many `search_products` calls per loop run? If basket has 15 items, that's 15 MCP calls — within rate limits?
- [ ] What is Swiggy MCP's rate limit per household per day? Need to confirm before designing retry logic.
- [ ] Should the LLM have access to Instamart MCP tools directly (agentic), or do we resolve SKUs separately after the LLM plans? Current design: separate — keeps LLM output clean and debuggable.
- [ ] How do we version the LLM prompt? A prompt change should not silently change basket behaviour for 100 households simultaneously.
- [ ] Edit learning: after how many weeks of consistent edits do we promote a pattern into the rules engine?
