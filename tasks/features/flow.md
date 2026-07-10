# Flow — Intelligent Replenishment
**Version 1.0 | PantryPilot | Status: Pre-build**

---

## Problem

The current PantryPilot planning loop generates baskets, but they feel generic. Users edit 5–8 items per basket because the AI doesn't know them — it has no model of what the household actually consumes, what they always keep stocked, or what "enough" means for them specifically.

The fix is not a better prompt. It's a persistent household model that gets built from every order and edit, making each basket more accurate than the last.

---

## What Flow Is

Flow is PantryPilot's intelligent replenishment mode. It answers one question at generation time:

> What is the gap between what this household has right now and what they need before their next expected order?

```
basket = adequate_state − current_state  (over horizon H)
```

The user's job is to confirm, not to build.

Flow is one of three ordering modes:

| Mode | User intent | AI role |
|---|---|---|
| **Flow** | Routine restocking | Generate, learn, improve |
| **Direct** | I know what I want | Get out of the way |
| **Explore** | Trying something new | Observe and remember |

This PRD covers Flow only.

---

## Target Users

**Regular user (primary)** — established rhythm, orders frequently. Currently edits 5–8 items per basket. Success: edits drop to 1–2 within 8 orders.

**Occasional user** — comes back after gaps. Should feel like the app remembers them even after 3 weeks away.

**New user** — no PantryPilot history, may have Swiggy history. First basket should be conservative and accurate, not ambitious and wrong.

---

## Household Model

A living profile updated after every confirmed basket. Five layers:

| Layer | What it captures | Update trigger |
|---|---|---|
| **Identity** | Size, diet, budget, cooking style, city | Onboarding + lifecycle signals |
| **Anchors** | Items the household must always have | Purchase signals + edit signals |
| **Velocity** | Consumption rate per item (packs/day) | Repurchase events + quantity edits |
| **Preferences** | Brand loyalty, pack sizes, exclusions, OOS fallbacks | Edit signals |
| **Edit patterns** | AI suggestion vs what user kept | Every confirm |

### Velocity tracking — two tracks

**Track 1 (repurchase-derived):** for items with repurchase interval < 30 days. Consumption rate = pack size / purchase interval. Works for milk, bread, eggs.

**Track 2 (LLM-seeded):** for items with repurchase interval > 30 days. Consumption rate seeded by LLM from pack size × category × cooking profile at first purchase. Corrected over time as repurchase events arrive. Handles bulk staples (atta, oil, hing) that would be invisible to a repurchase-frequency model.

All items start on Track 2. Graduate to Track 1 after first repurchase establishes a short-enough interval.

`pantry_items.avg_weekly_consumption` is the authoritative velocity store. `household_model` does not duplicate it.

---

## How Flow Works

### 1. Trigger

A Beat task `evaluate_flow_signals` runs periodically (every few hours) and evaluates per household:
- Anchor items estimated to deplete within N days
- Days elapsed relative to household's typical reorder interval
- Last order was smaller than usual (gap fills sooner)

When a signal fires, Flow generates the basket but **does not immediately deliver it**. Trigger time and delivery time are separate problems.

### 2. Delivery timing

Basket delivery is decoupled from trigger time. The basket is sent at the user's next high-attention window.

```
signal fires 11pm Tuesday
→ basket generated and held
→ delivered 7am Wednesday (user's morning confirm window)
```

Every household accumulates a `confirmation_behaviour` profile from the timestamps of every confirm action they have taken:

```
typical_confirm_hour_start: 8
typical_confirm_hour_end: 10
typical_confirm_days: weekday
avg_response_lag_minutes: 45
preferred_delivery_lead_hours: 12
```

Seeded from the first few orders. Refined over time. Default for new users: 7–9am delivery window.

**Confirmation timeout** — behaviour-derived, not hardcoded. Long enough to cover the user's typical response lag from their delivery window. 
Hard floor: the window must not expire before the user's next expected high-attention period. A window starting at 7am and running to 1pm is defensible. A window starting at 11pm is not.

### 3. Basket validation before delivery

The held basket captures **intent** (what the household needs at generation time). At delivery, Flow validates **execution** (can Swiggy fulfill this right now?). These run at different times.

**Three checks at delivery time:**
1. **Availability** — re-search each item via MCP, drop anything OOS
2. **Direct order reconciliation** — fetch order history since `generated_at`, remove items already covered by a household order in the hold window
3. **Price refresh** — update to current prices

The basket the user sees is the generated basket with OOS items dropped, already-ordered items removed, and prices refreshed.

**Staleness ceiling:** if `now − generated_at > 24h`, skip validation and trigger a full re-run. The hold window is for same-session gaps, not multi-day storage.

**Basket collapse:** if validation drops >50% of basket value, trigger a re-run instead of delivering. That volume of drops means conditions changed enough that the plan is no longer valid.

### 4. Item selection

**Tier 1 — Anchors:** always include unless obviously overstocked (`days_remaining > 2 × horizon`).

**Tier 2 — Velocity items:** include when:
```
days_remaining = estimated_qty / daily_velocity
include if days_remaining < horizon + buffer_days
```

**Tier 3 — Variable items:** include based on historical frequency, days since last ordered, and remaining budget after Tier 1 and 2.

**Explicit exclusions:** items removed from last 2+ baskets, items recently ordered with low velocity, items never purchased by this household.

### 5. Quantity computation

```
quantity = (horizon_days − days_remaining) × daily_velocity + buffer
```

Buffer is household-specific — derived from how much they typically overshoot vs undershoot. Quantities round to natural pack sizes.

### 6. Cold start

**User with Swiggy history** — fetch `get_order_history` at onboarding. Bootstrap velocity, anchors, preferences from real data. Not a cold start.

**User with Swiggy restaurant history only** — restaurant order patterns are a strong cultural proxy. A user who orders South Indian restaurants regularly is identifiable before any onboarding questions are asked. Pulled at auth time, costs nothing.

**User with no history** — archetype matching from standard profile (diet, household size, budget, city) does not work. "Vegetarian, family of 4, Bangalore, ₹2000/week" contains Tamil Brahmin, North Indian transplant, and simple-cooking households — three completely different staple sets. Templates would require hand-crafted cultural knowledge and would still be wrong for most households.

Instead: two targeted onboarding questions + LLM generation. The LLM carries the cultural knowledge. No templates needed.

**Added to onboarding (combined into one new screen, net +1 step):**
- **Cooking style** — "What do you cook most?" South Indian / North Indian / Simple / Mixed. Cultural routing, not a profile question.
- **Onion and garlic** — "Do you cook with onion and garlic?" Yes / No. Load-bearing constraint that changes the basket significantly.

**Placement:** after OTP, immediately before basket preview. "Answer two quick questions and we'll show you your first personalised basket." The reward is visible, dropout risk is low.

**What is not asked upfront:** cooking frequency. Moderate signal, inferable from basket 1 edits. A user who deletes all bulk staples is telling you their cooking profile without being asked.

**First basket framing:**

Correctness on basket 1 is impossible without data. The failure mode is under-specified, not wrong — correct at category level, unknown at variant level. Rice appears; sona masuri vs idli rice is unknown. Those edits are precisely targeted signal. "Replaced toor dal with masoor dal" lands directly in the preference layer with no ambiguity.

Cold start LLM prompt explicitly instructs: generate only high-confidence items; for ambiguous categories, include the most common variant; leave unknowns for the user to add.

**The first basket is a calibration prompt, not a recommendation.** User edits on basket 1 are the most valuable signal in the system — dense, from a user actively paying attention, before any habits have formed.

Framing in UI: if basket 1 is a recommendation the user is correcting, every edit registers as failure. If it's a starting point they are teaching from, the same edits feel like participation. Post-confirm message after basket 1 closes the loop: "Thanks — we've learned from your changes and will do better next time."

### 7. Learning loop

Edits are captured as `ItemSignal` rows in real time. At confirm, a reconciliation diff (AI-generated vs final confirmed basket) drives the actual model update. Real-time signals alone would misread remove-then-re-add as suppression; the confirm diff correctly sees no net change.

| Signal | Interpretation | Update |
|---|---|---|
| Item removed | Don't include / have enough | Suppress or reduce velocity |
| Quantity increased | Underestimated consumption | Velocity up |
| Quantity decreased | Overestimated | Velocity down |
| Item added (AI missed) | Should have caught this | Add to anchors or velocity list |
| Brand changed | Preference shift | Update preference layer |
| Confirmed with no edits | Everything right | Positive signal across all items |

**Product philosophy: transparent at the moment of consequence, invisible everywhere else.**

The system asks a question only when the cost of wrong inference is high enough that asking is less disruptive than getting it wrong. When it asks, it asks about intent — not the model. "Skip milk next time too?" is asking about intent. The user doesn't need to know there's a suppression decay loop or a regularity score.

---

## Anchor System

### Definition

An anchor is defined by **criticality** — if this item were missing, the household would notice immediately and it would be a problem. Not frequency, not consecutiveness.

A 10kg atta bag bought once every 6 weeks is the most critical anchor in many households. It appears in 1 of every 6 orders — never consecutively. Under a "3 consecutive orders" rule it looks like a Tier 3 variable item. That's wrong.

An anchor qualifies when:
- `purchase_quantity / consumption_velocity ≥ reorder_interval` — buys enough to last until the next order cycle (continuous consumption, not occasional use)
- When stock runs low, household reorders within their normal order window

### Detection

Criticality cannot be observed directly. The most reliable anchors are the ones that never cause problems — perfectly-managed staples produce no distress signals. Three structural signals that identify anchors without requiring distress:

**1. Purchase regularity**
```
regularity = 1 − (std_dev of purchase intervals / mean purchase interval)
```
High regularity + large purchase quantity → managed staple → anchor candidate.

**2. Co-purchase clustering**
Items that consistently co-purchase reveal cooking identity. A household buying atta + dal + oil + whole spices is a from-scratch daily cooking household. All of those items are structural anchors, inferable from the cluster even if individual item signals are quiet.

**3. Add-back after AI omission**
If Flow omits an item and the user manually adds it, that's a strong anchor signal even if it happens once.

For ambiguous cases: bias toward inclusion. Including an item that wasn't needed costs one user edit. Missing a critical item costs the household.

### Promotion and demotion — three-layer mechanism

**Layer 1 — Rules engine (real-time, deterministic):**
- Regularity score > 0.8 + large purchase quantity → direct promotion, no LLM
- Add-back after AI omission → immediate promotion, no LLM
- Item removed 2+ consecutive baskets → flagged for demotion review

**Layer 2 — LLM (async, interpretive):**
- Co-purchase cluster recognition — catches anchors the rules engine missed
- Demotion confirmation — only if cluster membership also weakened
- Runs at onboarding and when re-evaluation is triggered, not on every generation

**Layer 3 — persisted anchor list:**
```
Seed at onboarding (Swiggy history + LLM cluster interpretation)
    ↓
Rules engine promotes on regularity threshold (direct)
    ↓
Add-back events trigger immediate promotion (direct)
    ↓
LLM re-evaluates when drift conditions are met
    ↓
Consecutive removal events trigger demotion review
    ↓
Demotion confirmed if cluster membership also weakened
```

### Re-evaluation trigger

Not a fixed order count. Re-evaluate when:
```
new orders since last evaluation ≥ 5 (floor — prevents thrashing)
AND any drift signal is present:
    - a current anchor hasn't appeared in purchase_cycle × 2
    - a new item cluster has emerged not in anchor list
    - avg_edit_count has increased (model drifting from reality)
```

### Anchor status levels and removal responses

| Status | Definition | Removal response |
|---|---|---|
| Confirmed anchor | 3+ purchases, high regularity | Soft suppress → one-tap prompt if no substitution detected |
| Provisional anchor | 1-2 purchases, LLM-inferred at first purchase | Strong suppress immediately — prior was weak |
| Unclassified (user-added) | Excluded, user manually added | Promote to provisional anchor |

**One-tap prompt (narrow trigger):**
> "You removed milk from this order — skip it next time too?"
> **Yes** / **Just this once**

Fires only when: confirmed anchor + full removal + no substitution in same session. Non-anchor removals, quantity reductions, and brand substitutions never trigger it.

**Suppression decay:** suppression is not permanent. If an item was suppressed but not reordered via Direct within 2 cycles, it is re-introduced and observed. Removed again → suppress longer. Kept → removal was situational.

---

## Micro-Consumption Items

Items purchased in bulk with very low daily consumption (hing, turmeric, whole spices) are invisible to a repurchase-frequency velocity model. Hing bought once every 90 days looks identical to hing bought once and forgotten.

**Distinguishing signals:**

**Implied consumption rate:** pack size ÷ repurchase interval. 50g / 90 days = 0.56g/day — consistent with daily use. A genuinely forgotten item would show an implausibly low rate for its category, or no repurchase at all.

**Co-purchase cluster:** hing co-purchases with toor dal, mustard seeds, curry leaves. A household buying that cluster consistently uses hing daily. Cluster membership implies cooking identity; cooking identity implies daily use of the cluster's foundational spices. For confirmed cluster members, anchor status does not require purchase frequency to cross a threshold — cluster membership is sufficient.

**LLM category knowledge:** the LLM knows hing is a micro-consumption item. It can seed Track 2 velocity at first purchase without waiting for a repurchase signal.

**Classification ladder:**

| Evidence available | Confidence | Flow action |
|---|---|---|
| 1 purchase + strong co-purchase cluster match | High | Include as provisional anchor from basket 2 |
| 1 purchase + no cluster signal | None | Exclude until purchase 2 |
| 2 purchases (one interval derivable) | Moderate | Include at LLM-seeded velocity, wide uncertainty |
| 3 purchases (two intervals, variance computable) | High | Classify definitively |

For items unclassifiable at purchase 1: the gap is one repurchase cycle. If the household needed it during that window, they add it manually — the strongest possible classification signal, triggers immediate provisional anchor promotion.

---

## Brand Preferences

### What the preference layer stores per category

| Field | Description |
|---|---|
| `brand_loyalty_score` | Entropy of brand distribution across purchases. Low entropy = loyal. High entropy = flexible. |
| `preferred_brand` | Set only when loyalty score is high. Null for flexible categories. |
| `known_brands` | All brands the household has accepted. Acceptability list, not preference ranking. |
| `rejected_brands` | Brands explicitly removed without substitution. |
| `oos_fallbacks` | Per-brand fallback map: `{Nandini: [Heritage]}`. Contextual, not preference. |

### What Flow puts in the basket

**High loyalty category:** pick preferred brand, include a fallback.

**Low loyalty category:** pick from known brands by availability and price. Brand is a soft constraint. Do not force the plurality brand.

### Swap interpretation

Flow picks Amul, user swaps to Nandini. Three possible explanations: preference signal, price/availability signal, noise. Informationally identical from a single diff.

**OOS substitution** (remove A + add same-category B in same session): detected by substitution logic. Add B to `oos_fallbacks` for A, not to known brands as a peer.

**Price context:** if the swapped-to brand was cheaper at confirm time and household shows price sensitivity, the swap is value-driven.

**Pattern accumulation:** single swap in a low-loyalty category = noise. Three directional swaps = weak preference signal. Five = reorder known brands list.

| Context | Interpretation | Update |
|---|---|---|
| Low loyalty, single swap | Noise | No update |
| Low loyalty, 3+ directional swaps | Weak preference | Reorder known brands list |
| High loyalty, single swap | Meaningful deviation | Flag for monitoring |
| High loyalty, 2+ directional swaps | Preference shift | Update preferred brand |
| Remove A + add same-category B (same session) | OOS substitution | Add B as fallback for A only |
| Remove A, add nothing | Suppression or gap | Suppression rules apply |

---

## Lifecycle Events

### Why silent recalibration fails for step-function changes

Silent recalibration handles gradual drift correctly — velocity shifts slowly, brand preferences update over orders. It fails for step-function changes where the past is not a degraded guide but actively wrong.

Concrete failure: household goes vegetarian after years of ordering meat (confirmed anchors, strong velocity, co-purchase cluster including meat-cooking ingredients).

- **Basket N**: meat included → removed → soft suppress (next basket only). Anchor status unchanged.
- **Basket N+1**: meat excluded (soft suppress). Suppress expires.
- **Basket N+2**: meat re-included → removed again → demotion review flagged. LLM confirmation pending, requires floor of 5 new orders.
- **Baskets N+2 to N+6**: meat suppressed but anchor in limbo. Ginger-garlic paste, yogurt, meat-cooking spices still cluster members — still appearing. Household watches the AI recommend a cooking identity they've walked away from.
- **Basket N+5 to N+6**: LLM re-evaluation runs, cluster shift confirmed, demotion confirmed.
- **Baskets N+7 to N+10**: velocity fades, cluster reassignment propagates.

**Timeline: 7–10 weeks.** Household patience runs out around week 2.

The soft-suppress mechanic makes it worse — by suppressing meat in N+1, it delays the second removal that would trigger demotion review.

### User-initiated lifecycle signal

"Something changed" in settings. One tap, four event types:

| Event | System response |
|---|---|
| Dietary change | Update `diet_type` → `plan_rules` filter applies immediately. Mark non-compliant anchors `under_review`. Trigger LLM cluster re-analysis. One-cycle fix, not 7-week recalibration. |
| New household member | Update `member_count`. Flag velocity recalibration. Next basket partially cold — keep anchors, recalibrate quantities. |
| Returning from vacation | Suppress standard restock trigger for one cycle. Lean on velocity, not elapsed time. |
| Major restocking done | Depress trigger signals for the relevant horizon. |

`diet_type` must be settable from settings, not locked to onboarding. This is a prerequisite.

### Triggered fallback

When 3+ confirmed anchors are removed in a single basket with no substitutions detected, Flow surfaces:

> "A lot changed this order — did something shift for your household?"

One tap opens the settings signal flow. Fires only on discontinuity, not normal editing. Consistent with product philosophy: transparent at the moment of consequence.

---

## Planning Loop Changes

Current:
```
sense → plan_rules → plan_llm → optimize → confirm → place
```

Flow splits the pipeline into two passes separated by the hold window:

**Planning pass (at trigger time):**
```
build_household_context → sense → plan_rules → plan_llm → optimize → [hold]
```

**Delivery pass (at user's high-attention window):**
```
validate → confirm → place → update_household_model (async)
```

The hold between passes may be minutes or hours. `validate` is not part of the planning pass — it runs at delivery time, immediately before the basket is sent to the user.

**`build_household_context`** — assembles household model state: anchors, velocity per item, recent edit patterns, budget utilisation from last 3 orders, exclusion list, brand preferences per category. Injected into `plan_llm` prompt.

**`validate`** — delivery-time pass: availability check (drop OOS items), Direct order reconciliation (drop items already ordered since `generated_at`), price refresh. If >50% of basket value dropped, re-triggers the planning pass instead of delivering.

**`update_household_model`** — async, after confirm. Processes edit signals, updates velocity, anchors, preferences. No user-visible action.

Implementation order: data first (anchors, exclusions, edit patterns), then prompt restructuring. Two separable steps.

---

## Data Model

### `household_model` (new)

| Field | Type | Description |
|---|---|---|
| id | UUID PK | |
| household_id | UUID FK | One per household |
| anchors | JSONB | `[{item, status, promoted_by, promoted_at}]` |
| preferences | JSONB | Per-category: loyalty_score, preferred_brand, known_brands, rejected_brands, oos_fallbacks |
| confirmation_behaviour | JSONB | typical_confirm_hours, typical_confirm_days, avg_response_lag_minutes, preferred_delivery_lead_hours |
| avg_edit_count | Float | Rolling average, last 10 confirmed baskets |
| reorder_horizon_days | Int | Derived from household's actual reorder interval |
| last_evaluated_at | Timestamp | Last LLM re-evaluation |
| last_updated | Timestamp | |

### `flow_baskets` (new)

| Field | Type | Description |
|---|---|---|
| id | UUID PK | |
| household_id | UUID FK | |
| loop_run_id | UUID FK | |
| generated_at | Timestamp | When planning ran |
| validated_at | Timestamp | When delivery-time validation ran |
| delivered_at | Timestamp | When basket was sent to user |
| generated_items | JSONB | Basket at generation time: `[{item_name, sku_id, brand, quantity, unit, price_at_generation}]` |
| validated_items | JSONB | Basket after validation (what user sees) |
| dropped_items | JSONB | Items removed at validation + reason (oos / already_ordered) |
| status | Enum | `held` / `delivered` / `confirmed` / `expired` / `replaced` |

### `item_signals` (new)

| Field | Type | Description |
|---|---|---|
| id | UUID PK | |
| household_id | UUID FK | |
| loop_run_id | UUID FK | |
| item_name | String | |
| signal_type | Enum | `added` / `removed` / `qty_increased` / `qty_decreased` / `brand_changed` / `accepted` |
| previous_value | JSONB | Before state |
| new_value | JSONB | After state |
| recorded_at | Timestamp | |

Existing `pantry_items` continues as velocity ground truth.

---

## Files to Touch

| File | Change |
|---|---|
| `app/pilot/app/models/db.py` | Add `HouseholdModel`, `FlowBasket`, `ItemSignal` ORM models |
| `app/pilot/app/agent/planning_graph.py` | Add `build_household_context`, `validate` nodes |
| `app/pilot/app/services/household_model_service.py` | New — build context, process signals, update model, lifecycle handling |
| `app/pilot/app/api/basket.py` | Emit `ItemSignal` rows on confirm/edit; trigger lifecycle signal flow |
| `app/pilot/app/api/settings.py` | Add diet_type update + "Something changed" lifecycle endpoint |
| `app/cockpit/src/app/onboard/page.tsx` | Add "Tell us about your kitchen" screen |
| `app/cockpit/src/app/settings/page.tsx` | Add "Something changed" lifecycle entry point |
| `app/cockpit/src/app/dashboard/page.tsx` | Surface Flow mode — status, next run timing, recent basket summary |
| `migrations/` | New Alembic migration for three new tables |

---

## Success Metrics

Edit count alone is insufficient. A user who stops editing but places two Direct orders per week has low edit count and high cognitive load. The metric is lying.

Edit count is a leading indicator — necessary, not sufficient. Additional signals distinguish genuine model improvement from learned helplessness:

| Metric | Direction | What it tells you |
|---|---|---|
| Avg edits per basket | ↓ | Leading indicator. Necessary, not sufficient. |
| Edit count by order number | ↓ curve | Whether model improves per household over time |
| Reorder interval stability | Stable | Whether quantities are right |
| Direct order frequency between Flow runs | ↓ | Whether Flow is missing things the user compensates for |
| Items in Direct that appear in next Flow basket | ↑ | Whether Flow self-corrects after missed gaps |
| Confirm rate | ↑ | Baskets confirmed vs abandoned |
| Time to confirm | ↓ | Less editing = faster confirmation |
| Retention at 30 / 60 / 90 days | ↑ | Lagging truth |

**Primary metric:** retention at 90 days, segmented by edit count trajectory.
- Edit count ↓ + retention ↑ = model working
- Edit count ↓ + retention ↓ = learned helplessness, quiet churn
- Edit count flat + retention ↑ = engaged but model not improving fast enough

---

## Open Questions

1. Minimum order history before velocity estimates are reliable — hypothesis: 3 orders for Track 1, 1 purchase + LLM seed for Track 2.
2. Track 2 velocity seeds from LLM at first purchase, but LLM estimates carry uncertainty. How does the model detect when the seeded velocity is significantly wrong — before a second purchase confirms it? Candidate signal: user consistently adjusting quantity up or down across baskets. Needs a defined correction mechanism.
3. Should the household model surface any state to the user ("we think you buy milk every 4 days") — or remain fully opaque? Lean toward opaque; expose decisions not mechanisms.
