# UI-020 — Inference Screen Design Sync ("What We Know")

**Status:** ✅ Done  
**Area:** Frontend  
**Depends on:** UI-019 (smart onboarding flow — done)

---

## Problem

The current `Step4Inference` component is a flat list of `SummaryRow` items in a single grey card. The design reference (`design/ui/index.html`, Screen 5) shows a richer layout with:

- A full-width dark green gradient header (not a card header)
- Three distinct sectioned cards: "Your household", "Your go-to items", "Delivery address"
- Dot indicators on each row (pale green `#D8F3DC` dot, label left, value right in dark green `#2D6A4F`)
- Go-to items rendered as pill tags (not rows)
- "Does this look right?" framing with a prominent confirm button
- Icon is 🔍, not ✨

The current component also doesn't use `pantry_seeds` or `preferred_order_day` data that the infer API already returns.

---

## Design reference

`design/ui/index.html` → Screen 5 "What We Know" (nav button: "5. What We Know")

Key visual elements:

```
┌──────────────────────────────────────┐
│  [dark green gradient header]        │
│  🔍                                  │
│  Here's what we already know         │
│  Pulled from your Swiggy order hist. │
├──────────────────────────────────────┤
│  YOUR HOUSEHOLD                      │
│  ● Diet pattern          Vegetarian  │
│  ● Typical order day         Sunday  │
│  ● Avg weekly spend          ₹2,100  │
├──────────────────────────────────────┤
│  YOUR GO-TO ITEMS                    │
│  [Aashirvaad Atta] [Toor Dal] ...    │
│  [+ 6 more]                          │
├──────────────────────────────────────┤
│  DELIVERY ADDRESS                    │
│  ● 📍 Home — Koramangala, Bengaluru  │
├──────────────────────────────────────┤
│  Does this look right?               │
│  [✓ Yes, looks good]                 │
└──────────────────────────────────────┘
```

CSS from design file for reference:
- Header: `background: linear-gradient(135deg, #1B4332, #2D6A4F)` · `padding: 28px` · centered
- Card: `background: #F7F8F5` · `border-radius: 16px` · `padding: 20px` · `margin-bottom: 12px`
- Card title: `11px` · `font-weight: 600` · `color: #6B7280` (muted) · `uppercase` · `letter-spacing: 1px`
- Dot: `8px` circle · `background: #D8F3DC` (pale green-light, NOT dark green) · `margin-top: 5px`
- Row label: `13px` · `font-weight: 500` · `flex: 1`
- Row value: `13px` · `font-weight: 600` · `color: #2D6A4F` (dark green)
- Tag: `background: white` · `border: 1px solid #E5E7EB` · `border-radius: 8px` · `padding: 4px 10px` · `12px`

---

## Data available from `/onboard/infer`

| Field | Type | Used in design | Notes |
|---|---|---|---|
| `diet_type` | `string \| null` | "Diet pattern" row | Capitalize + replace `_` with space; skip row if null |
| `weekly_budget_max` | `number \| null` | "Avg weekly spend" row | Render as `₹{n}`; skip row if null |
| `preferred_order_day` | `string` (default `"sunday"`) | "Typical order day" row | Capitalize using `day.charAt(0).toUpperCase() + day.slice(1)` — always a single lowercase day name, so this is sufficient. No time-of-day suffix — the API does not return one. The design's "Sunday eves" is decoration; render just `"Sunday"`. |
| `pantry_seeds` | `list[dict]` — shape: `{item_name: string, category: string, qty: number, unit: string}` | "Your go-to items" tags | Extract `seed["item_name"]` for each tag label. Show up to 5 tags; if `pantry_seeds.length > 5`, append a `"+ {pantry_seeds.length - 5} more"` tag (e.g. 11 seeds → show 5 tags + `"+ 6 more"`). Hide card entirely if array is empty. |
| `address_line` | `string \| null` | "Delivery address" row | Full string; skip card if null |
| `has_order_history` | `boolean` | Drives which variant renders | If false: no-history variant (see below) |

**Not available:** `household_type` is not in the infer response — omit "Household type" row entirely. The design wireframe includes it but the API doesn't return it.

**Confidence percentage:** the design shows `"Vegetarian (94%)"` — this per-field confidence score is not returned by the infer API. Render diet type without a percentage: `"Vegetarian"`.

---

## No-history variant (`has_order_history = false`)

When infer returns `has_order_history: false`, skip the household and go-to-items cards. Show only:

```
┌──────────────────────────────────────┐
│  [same dark green gradient header]   │
│  🔍                                  │
│  Here's what we found                │
│  No previous Swiggy orders yet       │
├──────────────────────────────────────┤
│  DELIVERY ADDRESS (if available)     │
│  ● 📍 Home — Koramangala             │
├──────────────────────────────────────┤
│  We'll personalise your basket as    │
│  you shop. The more you order, the   │
│  smarter we get.                     │
├──────────────────────────────────────┤
│  [Got it →]                          │
└──────────────────────────────────────┘
```

---

## What changes

### `Step4Inference` (`app/cockpit/src/app/onboard/page.tsx`)

1. **Remove** the flat `SummaryRow` list and its wrapping card
2. **Add** dark green gradient header block — rendered full-width, outside the card's padded body (see layout approach below)
3. **History variant** — three sectioned cards:
   - "Your household": diet, preferred_order_day, weekly_budget_max rows with pale-green dot indicators; skip any row whose value is null/empty
   - "Your go-to items": pill tags from `pantry_seeds[*].item_name`; max 5 + `"+ N more"` overflow tag; hide card entirely if `pantry_seeds` is empty or absent
   - "Delivery address": single row with `address_line`; hide card if null
4. **"Does this look right?"** label centred above the CTA
5. **CTA:** single `"✓ Yes, looks good"` primary button. **No "Edit" link** — out of scope for this task; the design reference includes it but there is no destination defined for it yet
6. **Back link** rendered below CTA when `onBack` is provided (existing behaviour, keep)
7. **Remove `SummaryRow` helper** — verify no other call sites before deleting

**Props change — add `hasHistory`:**

```typescript
function Step4Inference({
  infer,
  hasHistory,
  onNext,
  onBack,
}: {
  infer: Record<string, unknown> | null
  hasHistory: boolean
  onNext: () => void
  onBack?: () => void
})
```

Pass `hasHistory={hasHistory}` from the root component (already in state).

### Layout approach for full-width gradient header

The `Card` component applies padding. To get a full-bleed header, `Step4Inference` must own its full outer shell — including the rounded card border. The `<Card>` wrapper in the root component must be conditional, wrapping all steps except inference:

```tsx
{currentStep === "inference"
  ? <Step4Inference ... />   {/* owns its own shell */}
  : (
    <Card>
      {currentStep !== "allset" && currentStep !== "inference" && (shared header)}
      {currentStep === "household" && <Step1Household ... />}
      {currentStep === "diet"      && <Step2Diet ... />}
      {/* ... other steps ... */}
    </Card>
  )
}
```

Inside `Step4Inference`, replicate the card's outer appearance (white background, rounded corners, shadow) then render the gradient header flush to the top:

```tsx
<div className="bg-white rounded-3xl shadow-sm overflow-hidden">
  <div style={{ background: "linear-gradient(135deg, #1B4332, #2D6A4F)" }}
       className="px-6 pt-8 pb-6 text-center">
    ...header content...
  </div>
  <div className="px-5 pb-6 space-y-3">
    ...cards...
  </div>
</div>
```

`overflow-hidden` on the outer wrapper ensures the gradient header corners respect the `rounded-3xl` clip.

### Section header in root component

Change:
```tsx
{currentStep !== "allset" && (
```
To:
```tsx
{currentStep !== "allset" && currentStep !== "inference" && (
```

`STEP_META["inference"]` title/subtitle/icon will no longer render in the shared header — they become dead code for the inference step but the entry can stay in the map (it doesn't hurt anything).

---

## Scope

- `Step4Inference` component only — no backend changes
- `SummaryRow` helper removed if no other call sites; check with grep before deleting
- Section header exclusion in root component (`!== "allset" && !== "inference"`)
- No changes to routing, flow logic, or other steps
- "Edit" link is explicitly **out of scope** — no destination defined yet

---

## Acceptance criteria

- [ ] Dark green gradient header (`linear-gradient(135deg, #1B4332, #2D6A4F)`) with 🔍 icon, "Here's what we already know", subtitle
- [ ] "Your household" card: diet row + preferred_order_day row + avg spend row; dots use `#D8F3DC`; values use `#2D6A4F`; rows hidden when value is null
- [ ] "Diet pattern" renders without confidence percentage (just `"Vegetarian"`, not `"Vegetarian (94%)"`)
- [ ] "Typical order day" renders capitalized day only (`"Sunday"`, no "eves" suffix)
- [ ] "Your go-to items" card: pill tags from `pantry_seeds[*].item_name`; up to 5 + `"+ {n - 5} more"` overflow tag; hidden when `pantry_seeds` is empty
- [ ] "Delivery address" card: `address_line` row; hidden when null
- [ ] "Does this look right?" label + "✓ Yes, looks good" CTA
- [ ] No "Edit" link rendered
- [ ] No-history variant: address card (if available) + personalisation message + "Got it →" button; household + go-to items cards absent
- [ ] Gradient header renders full-bleed (not inset inside card padding)
- [ ] `<Card>` in root component is conditional — inference step renders outside it and owns its own shell (`bg-white rounded-3xl shadow-sm overflow-hidden`)
- [ ] Section header in root component excludes `"inference"`
- [ ] `SummaryRow` helper removed (confirmed no other call sites)
- [ ] `hasHistory` prop wired from root component state
- [ ] No TypeScript errors
