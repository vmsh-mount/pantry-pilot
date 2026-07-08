# UI-021 — Layout Consistency System

**Status:** ✅ Done  
**Area:** Frontend only  
**Depends on:** UI-020 (inference screen — done)  
**Backend impact:** None

---

## Problem

Every page and onboarding step currently manages its own spacing, padding, and button placement from scratch. The result:

| Page / Step | Horizontal padding | Button area | Back button |
|---|---|---|---|
| Onboarding steps 1–3, 5–6 | `px-6` | Inline, `space-y-5` bottom | Ghost button, stacked below |
| Inference step (UI-020) | `px-5` body | Inline, `space-y-3` | Ghost button, stacked below |
| All Set screen | `px-6` | Inline, `space-y-5` | None |
| Dashboard banners | `px-4` | Scattered | N/A |
| Settings action buttons | `px-6` section + `space-y-2` for buttons | Two stacked buttons, no `pb-6` | N/A |
| Orders / Runs | `px-4` list rows | Button mid-page on empty state | N/A |

There is no shared definition of what "a card body" looks like, where CTAs sit, or how spacing is chosen.

---

## Goals

1. **One horizontal rhythm** — card body content uses `px-6`, enforced via a `CardBody` primitive
2. **One CTA pattern** — primary action always at the bottom of a card, never mid-page
3. **Consistent back navigation** — always ghost variant, always `← Back`, always below the CTA
4. **Defined spacing scale** — `space-y-*` and `gap-*` from a small fixed set
5. **No functional changes** — routing, API calls, state, and backend unchanged

---

## Layout rules

### 1. Horizontal padding

Card body content uses `px-6`. No `px-4` or `px-5` inside card bodies.

**Documented exception:** list rows inside a card (basket items, order rows, run rows) use `px-4` so row separator lines bleed edge-to-edge. This is intentional.

### 2. Card body rhythm — `CardBody` primitive

```
pt-5   top of content area
pb-6   bottom of content area (CTA sits here)
space-y-4  between content sections (default)
```

### 3. CTA placement

Primary CTA is always the last element in the card body, flush to `pb-6`. Never floating, fixed, or mid-page during normal flow.

### 4. Back navigation

Back button: ghost variant, label `← Back`, stacked directly below primary CTA via the shared `space-y-*` gap. Never rendered when `onBack` is undefined.

### 5. Spacing scale

| Token | Usage |
|---|---|
| `space-y-2` | OptionList rows (enforced inside the `OptionList` component in `ui.tsx` — already correct) |
| `space-y-3` | Tag groups, compact sections, action button pairs (e.g. settings account buttons — see below) |
| `space-y-4` | Standard `CardBody` rhythm, page-level card stacking |
| `space-y-5` | Onboarding questionnaire steps (override via `CardBody` spacing prop) |
| `gap-2` | Inline icon+text rows, banner elements |
| `gap-3` | Card row items, option grid cells |
| `gap-4` | Between top-level sections |

No values outside this set in card/component contexts. Raw one-off values (`space-y-1`, `space-y-1.5`, `gap-1.5`) are only acceptable inside self-contained sub-components (e.g. `Input` hint text, `StepBar` segments) — not in page layout.

**Audit command:**
```bash
grep -rn "space-y-\|gap-" app/cockpit/src/app/ | grep -v "node_modules\|\.next"
```
Verify all results fall within the allowed set above or are documented list-row exceptions.

---

## New primitive: `CardBody`

Add to `ui.tsx`:

```typescript
export function CardBody({
  children,
  spacing = "space-y-4",
  className = "",
}: {
  children: React.ReactNode
  spacing?: string
  className?: string
}) {
  return (
    <div className={`px-6 pt-5 pb-6 ${spacing} ${className}`}>
      {children}
    </div>
  )
}
```

**Why `spacing` is a separate prop (not inside `className`):** Tailwind class merging is based on stylesheet order, not DOM order. If both `space-y-4` (default) and `space-y-5` (override) appear in `className`, the stylesheet winner is unpredictable. The `spacing` prop replaces the default entirely — no conflict.

**Adoption policy:** `CardBody` is required for new components. Existing components that already match the rhythm (`px-6 pb-6 space-y-5`) are compliant and do not need to be migrated immediately. The acceptance criteria audit checks compliance with the rules, not adoption of the primitive.

---

## File-by-file changes

### `app/cockpit/src/components/ui.tsx`

- Add `CardBody` primitive (above)
- No changes to existing components

### `app/cockpit/src/app/onboard/page.tsx`

| Component | Current | Fix |
|---|---|---|
| Step1Household | `px-6 pb-6 space-y-5` | ✓ compliant, keep |
| Step2Diet | `px-6 pb-6 space-y-5` | ✓ compliant, keep |
| Step3Budget | `px-6 pb-6 space-y-5` | ✓ compliant, keep |
| **Step4Inference body** | `px-5 pb-6 pt-4 space-y-3` | → `px-6 pb-6 pt-5 space-y-4` (body div inside component's own shell — cannot use `CardBody` since the component owns its outer wrapper including the gradient header) |
| Step5Phone | `px-6 pb-6 space-y-4` | ✓ compliant, keep |
| Step6Otp | `px-6 pb-6 space-y-5` | ✓ compliant, keep |
| Step8AllSet | `px-6 pb-6 space-y-5` | ✓ compliant, keep |

Note: `Step4Inference` renders outside `<Card>` and owns its full shell (gradient header + body wrapper). `CardBody` cannot be used here — the body div is updated directly.

### `app/cockpit/src/app/dashboard/page.tsx`

Current state (verified by audit):
- Page-level card stack: `space-y-4` ✓
- Banners (dry-run, WA-off): `px-4 py-3` — these are full-width banner rows, not card body content. `px-4` is acceptable for banners that intentionally bleed closer to the card edge. Keep as-is — documented exception alongside list rows.
- Welcome card empty state: `px-6 py-10 space-y-4` ✓
- Stats/runs section: `px-6 py-8 space-y-3` — `space-y-3` is within scale ✓

No changes required to dashboard.

### `app/cockpit/src/app/settings/page.tsx`

Current state (verified):
- Section rows: `px-6 py-5 space-y-4` ✓
- Account action buttons: `space-y-2` between "Sign out" and "Delete account" buttons — change to `space-y-3`. `space-y-2` is reserved for `OptionList` rows; action button pairs use `space-y-3`.
- Buttons sit inside a `Section` with `px-6 py-5` — already at card bottom via section layout ✓

Change: `space-y-2` → `space-y-3` on the account buttons wrapper.

### `app/cockpit/src/app/orders/page.tsx`

Current state (verified):
- Empty state: button appears mid-card inside `space-y-3` content — acceptable since it's the only content in the empty state block. No change needed; the button IS at the bottom of the content.
- Order rows: `px-4 py-4` — list row exception ✓

No changes required to orders page.

### `app/cockpit/src/app/runs/page.tsx`

Confirmed exists (UI-017 shipped). Current state:
- Empty state button: inside `space-y-3` center block — same as orders, acceptable.
- Run rows: `px-4 py-3` — list row exception ✓
- Filter chips: `pb-1` on scroll row — keep

No changes required to runs page.

---

## What does NOT change

- `Shell`, `AppShell`, `Card`, `Button`, `StepBar`, `BudgetBar`, `OptionGrid`, `OptionList` — no changes
- `Step4Inference` gradient header padding (`px-6 pt-8 pb-6`) — intentional, untouched
- List row `px-4` — documented exception
- Banner `px-4` — documented exception
- All routing, state, API calls, backend

---

## Acceptance criteria

- [ ] `CardBody` primitive added to `ui.tsx` with `spacing` prop (not className override)
- [ ] `Step4Inference` body changed from `px-5 pb-6 pt-4 space-y-3` → `px-6 pb-6 pt-5 space-y-4`
- [ ] Settings account button wrapper changed from `space-y-2` → `space-y-3`
- [ ] All other pages confirmed compliant (no changes needed — verified above)
- [ ] Spacing audit passes: `grep -rn "space-y-\|gap-" app/cockpit/src/app/` returns only values from the allowed scale or documented exceptions
- [ ] Back button label is `← Back` everywhere it appears (grep for variant="ghost" and verify)
- [ ] No functional changes — all routes, actions, and API calls behave identically
- [ ] No TypeScript errors
