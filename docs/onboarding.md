# PantryPilot — Onboarding
*Last updated: 2026-06-25*

---

## Overview

Onboarding is the first experience a user has with PantryPilot. The goal is to get them from landing page to their first basket preview in under 3 minutes — without making them fill a long form.

The philosophy: **ask only what we cannot infer. Infer everything else from Swiggy order history.**

Onboarding happens entirely on the web app (pantrypilot.in). WhatsApp is linked at the end and takes over for all ongoing interactions.

---

## Onboarding Flow — Step by Step

### Step 1: Landing Page

User arrives at pantrypilot.in.

**What they see:**
- One-line pitch: *"Your weekly groceries, planned for you."*
- Single CTA: **"Connect your Swiggy account"**
- 3 trust signals below the fold: "No auto-orders without your approval", "Powered by Swiggy Instamart", "Cancel anytime"

**Design intent:**
- No sign-up form. No email. No password.
- Swiggy account IS the identity. We don't create a separate login.
- Trust signals are critical — users are nervous about an agent touching their grocery orders.

---

### Step 2: Swiggy OAuth

User taps "Connect your Swiggy account" → Swiggy OAuth 2.1 + PKCE flow.

→ Full details in [docs/auth.md](auth.md)

On successful auth, we immediately (in the background, while the user moves to Step 3):
- Call `get_orders` — pull last 6 months of Instamart order history
- Call `get_addresses` — fetch saved delivery addresses
- Begin inference pass (see Inference from Order History below)

---

### Step 3: The Questionnaire (30 seconds)

3 questions. Visual, tappable cards — not dropdowns or text fields.

**Question 1: Who's in your household?**

```
[ 🧍 Just me ]   [ 👫 Couple ]   [ 👨‍👩‍👧 Family ]   [ 🏠 Joint family ]
```

Maps to approximate member count:
- Just me → 1
- Couple → 2
- Family → 2–4 (follow-up: "Any kids?" yes/no)
- Joint family → 5+

**Question 2: How does your household eat?**

```
[ 🥦 Vegetarian ]   [ 🌱 Vegan ]   [ 🪔 Jain ]
```

*Note: V1 is vegetarian households only. Non-veg support in V1.5.*

If order history already strongly signals diet type (no non-veg SKUs in 6 months), pre-select the answer and let user confirm rather than re-ask.

**Question 3: Weekly grocery budget?**

```
[ Under ₹1,500 ]   [ ₹1,500 – 3,000 ]   [ ₹3,000 – 5,000 ]   [ ₹5,000+ ]
```

Pre-fill based on historical AOV if confident (>4 orders in history). User can adjust.

**What we deliberately do NOT ask at this stage:**
- Allergies (collected progressively — surfaced when a basket item triggers a concern)
- Brand preferences (inferred from order history)
- Delivery window (inferred from past order timing, confirmed later)
- Member ages / weights / activity level (V2 — nutrition engine)
- Specific dietary restrictions beyond the 3 types above (V2)

---

### Step 4: Inference Pass — What We Already Know

After the questionnaire, we show the user a summary of what we inferred from their order history. This is the "we already know you" moment — high trust signal.

**Example summary card:**

```
Here's what we learned from your Swiggy history 👇

🛒  You typically order ₹2,200 worth of groceries per week
📦  Your go-to items: Toor Dal, Aashirvaad Atta, Amul Butter, 
     Fresh Tomatoes, Onions (+14 more)
🕐  You usually order on Sunday evenings
📍  Delivering to: Koramangala, Bengaluru
```

Below the card:
> *"Does this look right?"* → **Yes, looks good** / **Let me adjust**

If user taps "Let me adjust" → simple edit screen: change address, budget, order day preference. Nothing more.

**What we infer from order history:**

| Signal | How We Infer It |
|---|---|
| Dietary pattern | Absence of non-veg SKUs across 10+ orders → vegetarian |
| Approximate household size | Order volume + variety (cross-checked with questionnaire answer) |
| Brand preferences | SKUs that appear in ≥ 60% of orders for a category |
| Preferred order day/time | Mode of past order creation timestamps |
| Preferred delivery window | Mode of past delivery slot selections |
| Budget range | Median AOV of last 8 orders |
| Core staples | SKUs ordered in ≥ 4 of last 6 orders |
| Occasional items | SKUs ordered 1–3 times in last 6 orders |

---

### Step 5: Link WhatsApp

One field. One OTP.

```
Your WhatsApp number
[ +91 __________ ]  →  [ Send OTP ]
```

- User enters number → receives a 6-digit OTP on WhatsApp from PantryPilot's business number
- OTP valid for 10 minutes
- On verification: WhatsApp number stored encrypted, linked to household

**Why we ask for this now and not later:**
- The next step (basket preview) sends a message to WhatsApp
- User should receive it immediately — completing the loop while they're engaged
- If we ask for WhatsApp after showing the basket preview, the "wow moment" is delayed

**Fallback:** If user skips WhatsApp linking, basket is shown only on the web app. WhatsApp can be linked later from settings. Planning loop still works — confirmations happen on web instead.

---

### Step 6: First Basket Preview — The Wow Moment

This is the payoff for everything above.

**What the user sees (on-screen):**

```
Here's your basket for this week 🛒

Based on what you usually buy + your ₹2,200 budget

  Aashirvaad Atta 5kg          ₹280
  Toor Dal 1kg                 ₹145
  Amul Butter 500g             ₹285
  Fresh Tomatoes 1kg           ₹52
  Onions 2kg                   ₹68
  Spinach 500g                 ₹35
  Curd 400g                    ₹72
  + 8 more items

  Estimated total: ₹1,940 of ₹2,200 budget

────────────────────────────────────
  [ 📲 Send to WhatsApp for review ]
  [ Set a weekly schedule instead  ]
────────────────────────────────────
```

**Design intent:**
- Show 5–6 items + "+N more" — enough to feel real, not overwhelming
- Show budget utilisation — reassures user we're respecting their constraint
- Two CTAs — immediate send vs. scheduled. No forced choice.
- Do NOT show a "Place Order" button here. This is a preview, not a checkout.

**If user taps "Send to WhatsApp for review":**
- Full basket card sent immediately to their WhatsApp
- On-screen confirmation: *"Sent! Check your WhatsApp to review and confirm."*
- Onboarding complete

**If user taps "Set a weekly schedule instead":**
- Simple day + time picker: *"When should we send your basket each week?"*
- Default pre-filled from inferred order day/time
- Confirm → onboarding complete

---

### Step 7: Onboarding Complete

User sees a simple confirmation screen:

```
You're all set 🎉

PantryPilot will send your grocery basket 
every Sunday at 10 AM on WhatsApp.

You'll always get to review before anything is ordered.
```

Single link: *"Open WhatsApp"* → deep link to WhatsApp chat with PantryPilot number.

---

## Edge Cases

### New user with no Swiggy order history

If `get_orders` returns empty or fewer than 2 orders:
- Skip the inference pass entirely
- Questionnaire becomes slightly longer: add a free-text field *"What do you usually buy every week? (e.g. dal, rice, vegetables, milk)"*
- First basket is built from category-level defaults for the household type + diet + budget
- Clearly labelled: *"This is a starting point — we'll get better as you use PantryPilot"*

### Order history exists but is very old (6+ months ago)

- Use history for brand/preference inference only
- Do not use for quantity or frequency inference
- Treat basket composition as fresh start, validated by questionnaire answers

### User selects "Joint family" (5+ members)

- Flag for manual review in closed beta — larger households have more complex dynamics
- Still onboard normally, but tag in admin dashboard for closer monitoring in first 2 weeks

### User wants to add allergies during onboarding

- Not in the main flow — but a small *"Add dietary restrictions or allergies"* optional link shown below the questionnaire
- Opens a simple multi-select: Lactose intolerant / Gluten-free / Diabetic-friendly / Nut allergy / Other
- Stored as hard-block exclusions in household profile

---

## Data Collected at Onboarding

| Field | Source | Storage |
|---|---|---|
| Swiggy access token | OAuth flow | Encrypted, server-side only |
| Household type | Questionnaire | Postgres — households table |
| Diet type | Questionnaire + inferred | Postgres — households table |
| Budget range | Questionnaire + inferred | Postgres — households table |
| Preferred order day/time | Inferred + confirmed | Postgres — household_preferences table |
| Preferred delivery address | `get_addresses` | Postgres — addresses table (Swiggy address ID stored, not raw address) |
| Brand preferences | Inferred from order history | Postgres — household_preferences table |
| Core staples list | Inferred from order history | Postgres — pantry_items table |
| WhatsApp number | User input + OTP verified | Encrypted, Postgres — households table |

---

## What Onboarding Does NOT Collect

- Email address (not needed — Swiggy is the identity)
- Payment details (handled entirely by Swiggy at checkout)
- Location beyond delivery address (no GPS, no tracking)
- Member names, ages, weights (V2 — nutrition engine)
- Meal logging or dietary diary (never — not that kind of app)

---

## Open Questions

- [ ] Do we use Swiggy's WhatsApp Business API or a third-party provider (e.g. Interakt, Wati) for WhatsApp messaging?
- [ ] What is the fallback if `get_orders` is slow or fails during onboarding? Do we show a loading state or skip inference?
- [ ] Should the budget question be a range (as above) or a free number input? Range is lower friction but less precise for the optimizer.
- [ ] How do we handle a user who has multiple Swiggy accounts (e.g. one personal, one shared)?
- [ ] What is the re-onboarding flow if a user deletes their account and reconnects?
