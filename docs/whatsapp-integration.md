# PantryPilot — WhatsApp Integration
*Last updated: 2026-06-25*

---

## Overview

WhatsApp is PantryPilot's primary interaction channel post-onboarding. Every basket confirmation, order receipt, re-auth nudge, and exception alert is delivered here. The web app is for onboarding only — once the user is set up, their entire relationship with PantryPilot lives in WhatsApp.

---

## Provider Decision

**V1: Interakt**

| Property | Detail |
|---|---|
| Provider | Interakt (interakt.ai) |
| Underlying API | Meta WhatsApp Business API (via Interakt as BSP) |
| Reason | Indian product, supports interactive buttons, faster approval than going direct, battle-tested in Indian commerce |
| Switch trigger | Move to Meta Cloud API direct when household count crosses ~500 |

---

## Meta WhatsApp API — Key Rules

### The 24-Hour Conversation Window

WhatsApp enforces a strict **24-hour session window**:

- If the user messaged us in the last 24 hours → we can send free-form messages
- If the user has NOT messaged us in the last 24 hours → we can ONLY send pre-approved **Message Templates**

**For PantryPilot this means:** almost every outbound message we send (basket cards, reminders, receipts) will be outside the 24-hour window and therefore requires a pre-approved template.

### Message Templates

- Templates must be approved by Meta before use
- Approval typically takes 1–3 business days
- Templates support variables (e.g. `{{1}}` for household name, `{{2}}` for basket total)
- Templates can include interactive components: buttons (up to 3), quick replies, CTAs
- Template category affects pricing: **Utility** (transactional) vs **Marketing**
  - All PantryPilot templates should be filed as **Utility** — they are transactional, not promotional

---

## WhatsApp Number Setup

- Dedicated phone number registered with Meta via Interakt
- Number used exclusively for PantryPilot — never shared with other services
- Display name: **PantryPilot** (verified business name shown to users)
- Users save this number during onboarding OTP step — familiarity builds trust

---

## Message Templates (V1)

Five templates required before launch. All filed as **Utility** category.

---

### Template 1: OTP Verification

**Purpose:** Verify user's WhatsApp number during onboarding (Step 5)

**Template name:** `pantrypilot_otp_verification`

**Message:**
```
Your PantryPilot verification code is: {{1}}

This code expires in 10 minutes. Do not share it with anyone.
```

**Variables:**
- `{{1}}` — 6-digit OTP

**Buttons:** None

**Notes:** OTP messages are a standard Meta template category — fastest approval.

---

### Template 2: Basket Preview Card

**Purpose:** Send the weekly basket suggestion for user review

**Template name:** `pantrypilot_basket_preview`

**Message:**
```
Hey! Your grocery basket for this week is ready 🛒

{{1}}

Estimated total: ₹{{2}} of your ₹{{3}} budget

Tap below to review or confirm 👇
```

**Variables:**
- `{{1}}` — Basket summary (top 5 items + "+N more items")
- `{{2}}` — Estimated basket total
- `{{3}}` — Household weekly budget

**Buttons (interactive — up to 3):**
```
[ ✅ Looks good, order it ]
[ ✏️ Let me review items  ]
[ ❌ Skip this week       ]
```

**Button behaviour:**
- **Looks good, order it** → triggers order placement flow (see Confirmation Flow below)
- **Let me review items** → sends full item list, user can reply with edits
- **Skip this week** → cancels this cycle, schedules next one

**Notes:** This is the most critical template. Get the wording right — it needs to feel like a helpful friend, not a bot.

---

### Template 3: Order Placed Receipt

**Purpose:** Confirm that the order was successfully placed on Swiggy Instamart

**Template name:** `pantrypilot_order_receipt`

**Message:**
```
Your groceries are on their way! 🎉

Order placed on Swiggy Instamart
Items: {{1}}
Total: ₹{{2}}
Delivery to: {{3}}
Expected by: {{4}}

Track your order on Swiggy anytime.
```

**Variables:**
- `{{1}}` — Number of items ordered
- `{{2}}` — Final order total
- `{{3}}` — Delivery address short name (e.g. "Koramangala")
- `{{4}}` — Expected delivery window (e.g. "Today, 6–8 PM")

**Buttons:**
```
[ 📦 Track on Swiggy ]
```

**Button behaviour:**
- **Track on Swiggy** → deep link to Swiggy order tracking page

---

### Template 4: Re-auth Reminder (48 Hours)

**Purpose:** Warn user that their Swiggy session expires in 48 hours — proactive re-auth

**Template name:** `pantrypilot_reauth_48hr`

**Message:**
```
Heads up! Your Swiggy connection expires in 2 days.

To keep your grocery automation running smoothly, reconnect your account before {{1}}.

Takes less than a minute 👇
```

**Variables:**
- `{{1}}` — Token expiry date/time (e.g. "Friday, 28 Jun at 9 AM")

**Buttons:**
```
[ 🔗 Reconnect Swiggy ]
```

**Button behaviour:**
- **Reconnect Swiggy** → deep link to pantrypilot.in/reauth with pre-filled household context

---

### Template 5: Re-auth Reminder (24 Hours / Urgent)

**Purpose:** Final warning before token expiry — more urgent tone

**Template name:** `pantrypilot_reauth_24hr`

**Message:**
```
⚠️ Your Swiggy connection expires tomorrow.

If you don't reconnect by {{1}}, we won't be able to place your grocery order this week.

Reconnect now (takes 60 seconds) 👇
```

**Variables:**
- `{{1}}` — Token expiry time (e.g. "Saturday, 29 Jun at 9 AM")

**Buttons:**
```
[ 🔗 Reconnect now ]
```

**Notes:** Deliberately more urgent than Template 4. Different wording so it doesn't feel like a duplicate.

---

### Template 6: Session Expired / Order Failed

**Purpose:** Alert when token has expired and an order attempt failed

**Template name:** `pantrypilot_session_expired`

**Message:**
```
We couldn't place your grocery order this week 😔

Your Swiggy session expired and we weren't able to connect. Reconnect your account to resume your weekly grocery planning.
```

**Variables:** None

**Buttons:**
```
[ 🔗 Reconnect Swiggy ]
```

---

## Conversation Flows

### Flow 1: Normal Weekly Confirmation

```
PantryPilot sends Basket Preview Card (Template 2)
        ↓
User taps "✅ Looks good, order it"
        ↓
PantryPilot: "Placing your order now... 🛒"  (free-form, within 24hr window)
        ↓
Order placed via Swiggy MCP (checkout tool)
        ↓
PantryPilot sends Order Receipt (Template 3)
```

---

### Flow 2: User Wants to Review / Edit

```
PantryPilot sends Basket Preview Card (Template 2)
        ↓
User taps "✏️ Let me review items"
        ↓
PantryPilot sends full item list as a numbered message:
  "Here's your full basket:
   1. Aashirvaad Atta 5kg — ₹280
   2. Toor Dal 1kg — ₹145
   3. Amul Butter 500g — ₹285
   ...
   Reply with item numbers to remove (e.g. "remove 3, 7")
   Or reply "add [item name]" to add something"
        ↓
User replies with edits
        ↓
PantryPilot confirms changes:
  "Got it! Updated basket:
   - Removed: Amul Butter 500g
   - Added: Amul Ghee 500g
   New total: ₹2,010
   
   Shall we place this order?"
   [ ✅ Yes, place it ]  [ ✏️ Edit more ]  [ ❌ Cancel ]
        ↓
User confirms → order placed → Receipt (Template 3)
```

---

### Flow 3: User Skips This Week

```
PantryPilot sends Basket Preview Card (Template 2)
        ↓
User taps "❌ Skip this week"
        ↓
PantryPilot: "No problem! We'll send your next basket on {{next_date}}.
              Reply 'order now' anytime if you change your mind."
        ↓
Next weekly cycle scheduled as normal
```

---

### Flow 4: No Response (Timeout)

If user does not interact with the basket card within **4 hours**:

```
PantryPilot sends a gentle nudge (free-form if within 24hr window):
  "Just checking — did you see your basket for this week?
   Tap below to confirm or skip 👇"
  [ ✅ Order it ]  [ ❌ Skip ]
        ↓
If no response after another 2 hours (6 hours total):
  → Auto-skip this week
  → Log timeout in household record
  → Next cycle scheduled as normal
  → No order placed
```

**Design intent:** We never place an order without user confirmation in V1. Timeout = skip, never timeout = auto-order.

---

### Flow 5: Re-auth Flow via WhatsApp

```
48hr before expiry: Template 4 sent
        ↓
User taps "🔗 Reconnect Swiggy"
        ↓
Opens pantrypilot.in/reauth in browser
        ↓
Full Swiggy OAuth flow (see auth.md)
        ↓
New token stored → WhatsApp confirmation:
  "You're reconnected! ✅ Your grocery planning will continue as usual."
        ↓
If user ignores 48hr reminder:
  24hr before expiry: Template 5 sent
        ↓
If user ignores again:
  At expiry: Template 6 sent (session expired)
  Planning loop paused until re-auth
```

---

## Inbound Message Handling

Users can reply to PantryPilot messages in free text. We need to handle:

| User says | PantryPilot does |
|---|---|
| "remove 3, 5" | Remove items 3 and 5 from basket, confirm |
| "add milk" | Search `search_products` for milk, add best match, confirm |
| "skip this week" | Cancel current cycle, schedule next |
| "cancel" / "stop" | Pause all activity, send confirmation |
| "pause" | Same as cancel — pause loop |
| "resume" | Resume paused loop, send next basket on schedule |
| "order now" | Trigger immediate basket generation outside schedule |
| "help" | Send list of available commands |
| Anything else | "I didn't quite get that. Reply 'help' to see what I can do." |

---

## Opt-out Handling

Meta requires all WhatsApp Business accounts to honour opt-outs immediately.

- User replies "STOP" or "Unsubscribe" → immediately halt all messages
- Stored as `whatsapp_opted_out: true` in household record
- Planning loop paused automatically
- User can re-opt-in from pantrypilot.in/settings

---

## Technical Integration with Interakt

### Sending a Message (Outbound)

```python
POST https://api.interakt.ai/v1/public/message/
Authorization: Basic <API_KEY>
Content-Type: application/json

{
  "countryCode": "+91",
  "phoneNumber": "9876543210",
  "callbackData": "household_id:abc123",
  "type": "Template",
  "template": {
    "name": "pantrypilot_basket_preview",
    "languageCode": "en",
    "bodyValues": [
      "Aashirvaad Atta 5kg, Toor Dal 1kg, Amul Butter (+8 more)",
      "1940",
      "2200"
    ]
  }
}
```

### Receiving a Message (Inbound Webhook)

Interakt sends a POST to our webhook endpoint when a user replies:

```
POST https://api.pantrypilot.in/webhooks/whatsapp
```

Payload includes: sender phone number, message text, button reply (if any), timestamp.

Our backend:
1. Identifies the household by phone number
2. Routes to the appropriate conversation handler
3. Responds within 5 seconds (WhatsApp expects fast replies)

### Webhook Security

- Validate Interakt's webhook signature on every inbound request
- Reject any request that fails signature validation
- Log all inbound messages (phone number hashed, message content retained for 30 days)

---

## Pricing Estimate (V1 Closed Beta)

Meta charges per conversation (24-hour window), not per message.

| Conversation type | Meta rate (India) | Est. per household/month |
|---|---|---|
| Utility (basket cards, receipts) | ~₹0.28 per conversation | ~4 conversations = ₹1.12 |
| Authentication (OTP) | ~₹0.15 per conversation | 1 at onboarding = ₹0.15 |

**For 100 households:** ~₹127/month in WhatsApp costs + Interakt subscription (~₹2,500–5,000/month for starter plan).

Total WhatsApp cost at 100 households: **< ₹5,500/month**. Negligible for beta.

---

## Open Questions

- [ ] Which Interakt plan covers our V1 needs? Confirm interactive button support on their starter tier.
- [ ] Do we need a verified Meta Business Account before Interakt can onboard us? Timeline?
- [ ] Template approval — submit all 6 templates at once or one at a time?
- [ ] What phone number do we register? A new SIM dedicated to PantryPilot or a virtual number?
- [ ] How do we handle users who block the PantryPilot number? Silent failure or detectable?
- [ ] Should the basket preview card include an image (e.g. a collage of top items)? Richer but more complex to generate.
