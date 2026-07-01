# UI-007 — Onboarding: Inference Summary Screen

**Status:** ⏳ Pending  
**Area:** Frontend  
**Depends on:** BE-001 (infer endpoint must return address_line)  
**Blocks:** UI-008

---

## Problem

After the 3 setup questions, the user is immediately asked for their WhatsApp number. There is no "here's what we already know about you" moment — which is the most compelling part of the product. The design dedicates a full screen (Screen 5) to this.

---

## Design Reference

Screen 5 in `design/ui/index.html` — "Inference Summary"

Header: dark green gradient, 🔍 icon, "Here's what we already know", "Pulled from your Swiggy order history"

Three info cards:
1. **Your household** — type, diet pattern (with confidence %), typical order day, avg weekly spend
2. **Your go-to items** — tag chips (item names from pantry bootstrap), "+ N more"
3. **Delivery address** — address line

Footer: "Does this look right?" + two buttons: "Yes, looks good" (→ next step) / "Edit" (→ back to questions)

---

## API

`GET /v1/onboard/infer` already returns inference data. After BE-001 is done it will also include `address_line`.

Response shape needed:
```json
{
  "household_type": "couple",
  "diet_type": "vegetarian",
  "diet_confidence": 0.94,
  "typical_order_day": "Sunday",
  "avg_weekly_spend": 2100,
  "staple_items": ["Aashirvaad Atta", "Toor Dal", "Amul Butter", "Tomatoes", "Onions"],
  "address_line": "Koramangala, Bengaluru"
}
```

---

## What to Build

Add an `inference` step to the onboarding wizard in `app/cockpit/src/app/onboard/page.tsx`:

- Insert between `budget` step and `whatsapp` step
- Call `api.onboard.infer()` when this step becomes active (or pre-fetch earlier)
- Show loading spinner while fetching
- Render three cards matching the design

Key UI details:
- Diet confidence shown as percentage: "Vegetarian (94%)"  
- Avg spend formatted: "₹2,100"
- Tag chips for items: show first 5, then "+ N more" chip
- "Edit" button goes back to step 1 (household)
- "Yes, looks good" advances to WhatsApp step

---

## Files to Touch

| File | Action |
|------|--------|
| `app/cockpit/src/app/onboard/page.tsx` | Add `inference` step |
| `app/cockpit/src/lib/api.ts` | Verify `onboard.infer()` exists (it does) |

---

## Acceptance Criteria

- [ ] Inference screen appears between budget and WhatsApp steps
- [ ] Household card shows all 4 rows with real data from API
- [ ] Go-to items shown as tag chips, capped at 5 + overflow count
- [ ] Address card shows human-readable address (not UUID)
- [ ] "Edit" goes back to household step (step 1)
- [ ] "Yes, looks good" advances to WhatsApp OTP step
