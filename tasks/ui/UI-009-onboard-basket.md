# UI-009 — Onboarding: Basket Preview Redesign

**Status:** ⏳ Pending  
**Area:** Frontend  
**Depends on:** BE-002 (category field in basket items)  
**Blocks:** UI-010

---

## Problem

The current basket preview step in onboarding renders a flat unformatted list of items. The design (Screen 7) shows a rich, grouped view with a budget bar, substitution warning, and a clear action stack.

---

## Design Reference

Screen 7 in `design/ui/index.html` — "First Basket Preview"

**Header (dark green):**
- Title: "Your basket this week 🛒" + item count badge
- Budget bar: label row ("Budget usage" / "₹1,940 of ₹2,200") + filled track bar

**Body:**
- Items grouped by category with section headers (STAPLES / FRESH PRODUCE / DAIRY etc.)
- Each item row: emoji icon + name + meta reason + price
- Substitution banner (amber) if any substitutions exist: "1 substitution: X unavailable → Y. Same brand, same size."
- "Show N more items" collapsed if > 5 items
- Total row with estimated total

**Action stack (bottom, white bg):**
- Primary: "📲 Send to WhatsApp for review"
- Secondary: "Set a weekly schedule instead"  
- Ghost: "Skip for now"

---

## Category → Emoji Map

```ts
const CATEGORY_EMOJI: Record<string, string> = {
  staples:    '🌾',
  dairy:      '🥛',
  vegetables: '🥬',
  fruits:     '🍎',
  spices:     '🌶️',
  bakery:     '🍞',
  beverages:  '🧃',
  snacks:     '🍪',
  cleaning:   '🧹',
  personal:   '🧴',
  grocery:    '🛒',   // fallback
}
```

---

## Item Meta Reason

For onboarding basket, derive from the item's `add_reason` field or:
- Rules engine items: "Running low · reorder"
- LLM additions: "✨ Suggested for you"
- Substitutions: handled by substitution banner, not inline

---

## Files to Touch

| File | Action |
|------|--------|
| `app/cockpit/src/app/onboard/page.tsx` | Replace flat list with grouped layout |
| `app/cockpit/src/components/ui.tsx` | Add `BudgetBar` component (reusable for dashboard too) |
| `app/cockpit/src/components/ui.tsx` | Add `SubstitutionBanner` component |

---

## Acceptance Criteria

- [ ] Items grouped under category section headers
- [ ] Budget bar shows estimated total vs `weekly_budget_max` from profile
- [ ] Amber substitution banner appears only when `is_substitution` items exist
- [ ] "Show N more" collapses items beyond 5
- [ ] Action stack: primary / secondary / ghost buttons match design
- [ ] "Skip for now" marks onboarding complete without sending basket
