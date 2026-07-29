# PantryPilot × Swiggy Demo — Shot-by-Shot Script

**Target runtime:** 8–12 min · **Format:** scripted screen recording, you narrate live
**Household:** `4327fc54-4b3b-4a83-82b3-5723b7f48465` — already reset to pre-onboarding state
**Backend mode:** `SWIGGY_MCP_MODE=demo` (curated catalog, no dependency on live Swiggy MCP — see [demo_catalog.py](../../app/pilot/app/mcp/demo_catalog.py)), `PANTRYPILOT_DRY_RUN=true` (checkout is faked, no real orders/money)

---

## Setup checklist — do this before hitting record

1. **Confirm state is clean**: `GET /v1/onboard/status` for the household above should show `onboarding_complete: false`, `household_type: null`. If not, ask me to reset it again.
2. **Confirm demo mode is on**: pilot logs should show `demo_mcp_call` entries, not `mcp_call` with `status=403`, when you touch anything Swiggy-related.
3. Close any other browser tabs open on `localhost:3000` for this household — a second tab racing the recording will cause exactly the kind of state clobbering we hit earlier.
4. Have your **real phone** ready to receive the WhatsApp OTP (Twilio sandbox — if you haven't joined the sandbox recently, send "join `<word>`" to +14155238886 first, or onboarding's OTP step will silently fail).
5. **Household members are not collectable through the UI** — they only affect personalized nutrition targets. Run this once, off-camera, right after Scene 2 (onboarding complete) and before Scene 3 (Flow order):

```bash
cd app && docker compose exec -T pilot python3 -c "
import asyncio, uuid
from app.database import AsyncSessionLocal
from app.models.db import HouseholdMember
from sqlalchemy import update
from app.models.db import Household

HOUSEHOLD_ID = '4327fc54-4b3b-4a83-82b3-5723b7f48465'

async def main():
    async with AsyncSessionLocal() as db:
        db.add(HouseholdMember(id=str(uuid.uuid4()), household_id=HOUSEHOLD_ID, role='adult',
            age_years=36, sex='male', weight_kg=78, height_cm=175, activity_level='very_active'))
        db.add(HouseholdMember(id=str(uuid.uuid4()), household_id=HOUSEHOLD_ID, role='adult',
            age_years=34, sex='female', weight_kg=60, height_cm=162, activity_level='sedentary'))
        # Deliberately no weight/height — shows the 'estimated' fallback tag on the Targets screen
        db.add(HouseholdMember(id=str(uuid.uuid4()), household_id=HOUSEHOLD_ID, role='child', age_years=9))
        await db.execute(update(Household).where(Household.id == HOUSEHOLD_ID).values(member_count=3))
        await db.commit()
        print('3 members seeded.')

asyncio.run(main())
"
```

This gives per-member target variety (one very-active adult with a higher protein bar, one sedentary adult, one child) and a genuine "estimated" badge next to the child — the Household Targets screen (Scene 9) shows one real personalized/estimated contrast instead of three identical rows.

6. Set household_type to **"family"** in Scene 1 step 2 (matches the 3 members above).

---

## Scene-by-scene

### Scene 1 — Cold open (0:00–0:20)
**Screen:** Land on `localhost:3000` while logged out, or a title card.
**Narration:** *"This is PantryPilot — it plans, confirms, and places your weekly grocery order automatically. But it doesn't stop at 'order placed' — it knows what you ate, and closes the gap on what you're missing. Let's walk through it end to end."*
**Action:** Cut to onboarding start.

### Scene 2 — Onboarding (0:20–2:00)
**Screen:** `/onboard`
1. Welcome screen → click through.
2. **Household type**: select **Family**.
3. **Diet type**: select **Non-Vegetarian** (opens up eggs/chicken in later scenes for protein variety).
4. **Inference step**: let it run — this is calling `get_addresses` + `get_orders` through demo mode, so it'll resolve instantly to the seeded demo address (*402, Willow Residency, Indiranagar*) with "no past order history found" — narrate this as "first-time setup."
5. **Phone number**: enter your real WhatsApp number.
6. **OTP verify**: real OTP sent to your phone — enter it live. *(This is the one step that genuinely depends on real infra — Twilio, not Swiggy — so it's unaffected by the MCP outage.)*
7. **Basket preview**: shows a first-cut suggested basket from the demo catalog's go-to items (rice, atta, toor dal, milk, curd, tomato, onion, oil).
8. **Complete** → lands on `/dashboard`, empty state.

**Narration beat:** *"Onboarding took under two minutes — no forms about pantry inventory, no manual catalog browsing. It inferred the address, the diet, and a starter basket on its own."*

**⏸ Run the household-member seeding snippet from the setup checklist now, off camera.**

### Scene 3 — Flow order, with a live edit (2:00–4:00)
**Screen:** `/dashboard` → trigger a planning run → `/flow`.
**Narration:** *"Flow is the core loop — sense pantry, plan against rules and an LLM, optimize against price and stock — and it stops for you before it ever spends money."*
**Action:**
1. Trigger the run, show the LoopRun progressing through states if the UI surfaces them (`sensing → planning → optimizing → awaiting_confirmation`).
2. Land on `/flow` with the basket in `awaiting_confirmation` — **this is the edit step, don't skip it**: it's the difference between "autonomous" and "a black box."
   - **Remove one item** using the in-app remove button (pick whichever staple looks least essential — e.g. sugar or salt).
   - **Add one item** via the in-app add/search — search **"besan"** and add Rajdhani Besan (a staple, not protein/iron/fiber-dense — deliberately kept nutritionally neutral so it doesn't prematurely close the gaps Scene 8 depends on; avoid chicken/soya/spinach/oats here for the same reason).
   - Point out the edit-summary banner ("Removed 1, Added 1") — narrate: *"Every edit here is a real signal — it teaches the model your preferences for next time, not just this basket."*
   - Mention in passing that the same edit is possible from WhatsApp directly (`remove 2`, `add besan`) — no need to demonstrate both channels on camera, one is enough.
3. Confirm the basket (in-app confirm button, or cut to WhatsApp to show the same confirm happening there — pick one).
4. Show `state: confirmed → placed` and the order landing in `/orders`.

**Expected contents** (demo catalog, pantry started empty, plus your edit): staples + dairy + vegetables from the initial plan (rice, atta or toor dal, milk, curd, tomato/onion), minus whatever you removed, plus besan.

### Scene 4 — Nutrition Snapshot (4:00–5:00)
**Screen:** Open the just-placed order's detail / nutrition card.
**Narration:** *"The moment an order lands, PantryPilot resolves it nutritionally — not a generic wellness score, actual calories, macros, and per-item confidence."*
**Action:** Point out:
- Calorie/protein/carb/fat/fiber totals for the order.
- Per-item confidence badges — packaged items (Amul, Tata, Aashirvaad) should show a higher-confidence tier than unbranded produce (tomato/onion) showing AI-estimated. This contrast is deliberate — narrate it as "labelled products get verified nutrition data, fresh produce gets a modeled estimate — and it tells you which is which."

Give this ~10s longer than you think you need — nutrition resolution runs as a background task right after checkout, so if the numbers aren't there the instant you land on the page, wait rather than reload.

### Scene 5 — Quick Order (5:00–6:00)
**Screen:** `/quick`
**Narration:** *"Not every purchase should wait for the weekly plan. Quick Order is direct — search, add, checkout."*
**Action — search and add exactly these 4** (deliberately category-diverse — dairy/protein, fresh produce, packaged, and the grocery catch-all — and deliberately light on iron/fiber so the nutrition gap story in Scene 8 is real, not staged):
- Search **"paneer"** → add Amul Malai Paneer
- Search **"banana"** → add Banana
- Search **"bhujia"** → add Haldiram's Aloo Bhujia
- Search **"detergent"** → add Surf Excel Detergent

Checkout. Point out the total, the "usually ready in minutes" framing vs. Flow's full planning cycle. Keep this scene tight — 4 items is enough to prove the path works without dragging the pace.

### Scene 6 — Routine (6:00–7:00)
**Screen:** `/routines/new`
**Narration:** *"For anything genuinely recurring, Routines skip the planning step entirely — set it once, it fires on schedule."*
**Action:** Search works the same way as Quick Order here (both go through the same demo catalog now). Create a routine — e.g. **"Weekly Milk & Curd Top-up"**, weekly frequency, items: Amul Toned Milk + Mother Dairy Curd. Save, show it listed in `/routines` with its next run date.
**Narration beat:** *"This doesn't need to fire on camera — it's on a schedule, same as the rest of your week."*

### Scene 7 — Pantry (7:00–7:45)
**Screen:** `/pantry`
**Narration:** *"Every order updates pantry state automatically — categorized, with stock estimates decaying over time."*
**Action:** Scroll through categories now populated from the two orders — expect **staples, dairy, fresh produce, packaged, and grocery** (the app's 5 real pantry buckets — non-dairy protein like chicken/soya and personal-care/cleaning items land in "grocery," everything else sorts into the other 4). Point out the category grouping itself, not just the item count.

### Scene 8 — Weekly Digest → Gaps → Fix (7:45–9:30) — the payoff
**Screen:** `/dashboard` → Nutrition card.
**Narration:** *"And this is the part nobody else does. It's not just tracking what you ate — it's telling you what's missing, and it puts the fix directly in your cart."*
**Action:**
1. Dashboard Nutrition card — chip should read **"needs attention"** (expected: protein short for the very-active member, iron and fiber short household-wide, given nothing iron/fiber-dense was ordered).
2. Click **"This week's report"** → weekly digest: macro bars vs. target, sodium ceiling, "Flagged this week" section listing the short nutrients.
3. Click **"Fix these in my cart"** → Gap-to-Cart screen: ranked recommendations per gap (expect spinach/dates for iron, oats/muesli for fiber, chicken/soya chunks for protein — all present in the demo catalog specifically for this).
4. Click **"Add all to cart"** → completion banner → **"Review Quick Order"**.
5. Cut back to dashboard — chip should now flip toward **"on track"** (or show the remaining smaller gap if not everything was added).

This is the single most important sequence in the video — don't rush it.

### Scene 9 — Household Targets (9:30–10:00)
**Screen:** `/settings/targets`
**Narration:** *"And it's per-person, not per-household-average."*
**Action:** Show the three seeded members: the very-active adult with a visibly higher calorie/protein target, the sedentary adult lower, and the child tagged **"estimated"** next to the other two "personalized" rows — narrate that distinction directly ("real biometrics get a personalized number, missing data still gets a sane estimate, and it tells you which").

### Scene 10 — Close (10:00–10:20)
**Screen:** Dashboard, full-circle.
**Narration:** *"Onboarding to order to nutrition resolution to gap detection to cart — closed loop, every week, with no manual tracking."* Cut to a title/CTA card.

---

## After recording

Tell me and I'll:
1. Wipe this household back to a clean pre-onboarding state again (same reset script) so it's ready for a re-take if needed.
2. Pull screenshots from your recording at each scene boundary above for the PRD PDF, or re-drive the app myself to capture matching stills if you'd rather not scrub your own footage.

## Known constraints to narrate around, not hide

- Swiggy MCP has been returning 403 on `get_orders`/`get_addresses` since 2026-07-26 — this recording runs on a curated demo catalog instead (`SWIGGY_MCP_MODE=demo`), not live Swiggy data. If Swiggy's team asks, that's the honest answer: same code path, same parsing logic, swapped data source, and full transparency about why.
- Checkout is dry-run (`PANTRYPILOT_DRY_RUN=true`) — no real orders are placed, no real money moves. Worth saying once on camera rather than leaving it ambiguous.
