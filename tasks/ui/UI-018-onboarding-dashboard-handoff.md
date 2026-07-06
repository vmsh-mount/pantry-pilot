# UI-018 — Onboarding → Dashboard Handoff

**Status:** ⏳ Pending  
**Area:** Frontend  
**Depends on:** BE-005 (whatsapp_enabled flag — done), UI-017 (runs endpoint — done)

---

## Problem

The onboarding flow ends abruptly and the user lands on the dashboard with no context about what just happened or what to expect next. Specifically:

1. **The "placing" screen lies.** `onboard/placing` shows "Building your basket on Swiggy Instamart" for 4 seconds, then redirects to `onboard/done` unconditionally — regardless of whether the pipeline is actually running, completed, or still pending. The user thinks an order was placed. It wasn't — it was queued.

2. **`onboard/done` is a dead end.** It says "Your first basket will be ready on your usual order day. We'll send it to WhatsApp." When WhatsApp is disabled (current dev default), this is wrong — no WA message is coming. The page has one button: "Go to Dashboard →". There is no state context passed forward.

3. **The dashboard greets a new user with "No basket pending."** When the pipeline is still in-progress (e.g. `place_order_now=true`), the in-progress state works. But if the user landed via "Schedule for my usual day", `in_progress=false` and the dashboard shows an empty 🛒 card with "Use Plan now above to generate your basket" — which makes no sense for someone who just finished onboarding.

4. **Step 8 copy is misleading.** "📲 Send to WhatsApp for review" on the basket preview step implies WhatsApp will be used. "💬 Sent to WhatsApp for your approval" in the How it works card on Step 8 is flat-out wrong when WA is disabled.

5. **The two Step 8 buttons diverge to the same place.** "Place my first order now" → `onboard/placing` → `onboard/done` → dashboard. "Schedule for usual day" → `onboard/done` → dashboard. Both land on the same generic done screen, losing the distinction between "your basket is running now" vs "you'll get it on Sunday."

---

## Goals

- Land the user on the **dashboard** directly from Step 8 — no intermediate dead-end pages
- The dashboard must show the **right context** for a just-onboarded user:
  - If pipeline is running → in-progress card (already works)
  - If scheduled → "Your first basket is scheduled for {day}" card instead of "No basket pending"
- Step 8 copy and the All Set screen must **respect `whatsapp_enabled`**
- The transition must feel intentional and complete — no "what do I do now?" moment

---

## Proposed flow

### Path A: "Plan now" (place_order_now = true)

```
Step 8 → POST /onboard/complete { place_order_now: true }
       → redirect to /dashboard (pipeline already running)
       → dashboard shows in-progress card + polling
```

The 4-second "placing" spinner is removed. The dashboard's existing in-progress card handles this state perfectly.

### Path B: "Schedule for usual day" (place_order_now = false)

```
Step 8 → POST /onboard/complete { place_order_now: false }
       → redirect to /dashboard
       → dashboard shows "Your first basket is scheduled" card
```

The `onboard/done` page is retired. The dashboard becomes the single landing point.

---

## Changes

### Step 7 (basket preview) copy

Current CTA: `"📲 Send to WhatsApp for review"` — implies WA.

Replace with a single neutral CTA regardless of WA state: **`"Continue →"`**

The basket preview step advances the wizard — the WA message (if enabled) fires in the background after `POST /onboard/complete`, not at this step. Adding a 📲 emoji here implies WA sends at this moment, which is wrong. Using one CTA for both states avoids a conditional and keeps the copy honest.

Remove the `"Nothing is ordered yet. You'll confirm on WhatsApp first."` note at the bottom when WA is disabled.

### Step 8 (All Set) — `whatsapp_enabled=false` variant

The current "How it works" list hardcodes WA steps. When disabled, replace with:

```
📅  Basket planned every week on your order day
✅  Review and confirm here in the app
⏸️  Pause or cancel any time in Settings
```

Remove the `"💬 Sent to WhatsApp for your approval"` row.

The two CTAs stay as-is: "Place my first order now" and "Schedule for my usual day" — these are still accurate regardless of WA.

### `handleComplete` in `onboard/page.tsx`

```typescript
// Before:
router.push(placeNow ? "/onboard/placing" : "/onboard/done")

// After:
router.push("/dashboard")
```

Both paths land on dashboard. The `/onboard/placing` and `/onboard/done` pages are no longer reachable from onboarding — they can be removed or left as-is (they don't cause harm if accessed directly).

### Dashboard — first-session welcome state

`GET /v1/basket/pending` already returns `next_run_at` in the `NoPendingBasket` response. The dashboard currently renders "No basket pending" when `in_progress=false` regardless of `next_run_at`.

Add a **scheduled welcome card** that shows when:
- `basket.pending = false`
- `basket.in_progress = false`
- `basket.last_failed = false` ← confirmed present in `GET /basket/pending` response
- `runsData.stats.total_runs === 0` (never completed a run — first-time user)
- `basket.next_run_at` is set ← use `basket.next_run_at`, NOT `runsData.next_run_at` (see note below)

**`next_run_at` source:** use `basket.next_run_at` from `GET /basket/pending` exclusively. Both `basket/pending` and `GET /v1/runs` return this field — using both creates a consistency risk if they diverge. `basket/pending` is already fetched on every load; `GET /v1/runs` is fetched separately. Use `basket/pending` as the single source of truth.

**`runsData` null guard:** `GET /v1/runs` loads in parallel with basket and may arrive late or fail. `runsData` can be null when the welcome card condition is evaluated. Guard: `(runsData?.stats?.total_runs ?? 0) === 0` — treat missing runs data as zero runs (safe default for first-time users).

**"Plan now instead →" button:** calls `POST /basket/trigger` (same as the existing "Plan now" button). Must be disabled when any run is already in-progress — check `basket.in_progress` before calling. Since the welcome card only shows when `in_progress = false`, the button is always enabled when the card is visible. No extra guard needed, but document this dependency so it doesn't get broken if the condition changes.

Card content:
```
🎉  Welcome to PantryPilot!
    Your first basket is scheduled for {day, date}.
    We'll plan it automatically — nothing to do until then.
    
    [Plan now instead →]
```

For returning users who just have no pending basket (`total_runs > 0`), keep the existing "No basket pending" card unchanged.

### `next_run_at` display format

`next_run_at` is a UTC ISO8601 timestamp. **Always convert to local time before comparing** — a run scheduled for Sunday 01:00 UTC would show as Saturday for IST (UTC+5:30) users without conversion. Use `new Date(next_run_at)` which respects the browser's local timezone.

Use a friendly label derived from the actual day name in the date (do not hardcode "Sunday"):
- Same local calendar day → `"today"`
- Next local calendar day → `"tomorrow"`
- Within 7 days → `"this {DayName}"` (e.g. `"this Wednesday"`)
- Further → `"{DayName}, {D} {Mon}"` (e.g. `"Sun, 13 Jul"`)

---

## Files to touch

**Frontend only:**
- `app/cockpit/src/app/onboard/page.tsx`
  - `handleComplete` redirect
  - Step 7 CTA copy (WA-aware)
  - Step 8 How-it-works list (WA-aware)
- `app/cockpit/src/app/dashboard/page.tsx`
  - First-session welcome card (when `total_runs === 0` + `next_run_at` set)
  - `next_run_at` friendly date formatter

**No backend changes needed** — all required data (`next_run_at`, `total_runs`, `in_progress`) is already returned by existing endpoints.

**Pages to keep (do not delete):**
- `app/cockpit/src/app/onboard/placing/page.tsx` — no longer reachable from onboarding, but must not crash if accessed directly (acceptance criteria)
- `app/cockpit/src/app/onboard/done/page.tsx` — same

---

## Acceptance criteria

- [ ] "Place my first order now" → lands on dashboard with in-progress pipeline card
- [ ] "Schedule for usual day" → lands on dashboard with first-session scheduled card showing the right day
- [ ] First-session scheduled card shows friendly date (`"this Sunday"`, `"tomorrow"`, etc.)
- [ ] First-session card absent once `total_runs > 0` — returning users see normal empty state
- [ ] Step 7 CTA copy does not mention WhatsApp when `whatsapp_enabled=false`
- [ ] Step 8 How-it-works list removes WA row when `whatsapp_enabled=false`
- [ ] No intermediate `/onboard/placing` or `/onboard/done` pages in the happy path
- [ ] `onboard/done` and `onboard/placing` pages still render if accessed directly (no crash)

---

## Out of scope

- Animated transition between onboarding and dashboard
- Onboarding re-entry / edit flow (settings page handles profile edits)
- Push notification or email fallback when WA is disabled
