# UI-002 — Layout Constraint + Design Tokens

**Status:** ⏳ Pending  
**Area:** Frontend  
**Depends on:** nothing  
**Blocks:** all other UI tasks (sets the visual foundation)

---

## Problem

1. **Layout distorts on wide screens.** Pages stretch to 100% browser width. The design targets a 375px phone. On a 1440px desktop, content spans edge-to-edge and looks broken.
2. **Wrong colour palette.** Tailwind classes like `green-700`, `green-900` don't match the design's specific hex values. The orange accent (`#F4845F`) is completely unused.

---

## Design Tokens (from `design/ui/index.html`)

```
--green:       #2D6A4F   primary — headers, buttons, borders
--green-light: #52B788   accents, dots, tags
--green-pale:  #D8F3DC   selected state backgrounds
--orange:      #F4845F   logo accent, warning CTAs  
--orange-pale: #FFF0EB   substitution banner background
--bg:          #F7F8F5   page background, card fill areas
--text:        #1A1A1A   body text
--muted:       #6B7280   secondary text, labels
--border:      #E5E7EB   dividers, input borders
```

---

## What to Build

### 1. `app/cockpit/tailwind.config.ts` — extend theme

```ts
theme: {
  extend: {
    colors: {
      pp: {
        green:        '#2D6A4F',
        'green-light':'#52B788',
        'green-pale': '#D8F3DC',
        orange:       '#F4845F',
        'orange-pale':'#FFF0EB',
        bg:           '#F7F8F5',
        text:         '#1A1A1A',
        muted:        '#6B7280',
        border:       '#E5E7EB',
      }
    }
  }
}
```

### 2. `app/cockpit/src/components/ui.tsx` — fix `Shell`

Current:
```tsx
<div className="min-h-screen bg-gradient-to-b from-green-900 to-green-800 p-4">
```

Target:
```tsx
<div className="min-h-screen bg-[#2D6A4F]">           {/* outer — fills viewport */}
  <div className="max-w-sm mx-auto min-h-screen bg-[#F7F8F5] relative">
    {children}
  </div>
</div>
```

This centres a 384px (max-w-sm) content column on all screen sizes. On a real phone it fills edge-to-edge.

### 3. Update all existing hardcoded colour classes

Replace across `ui.tsx`, `onboard/page.tsx`, `settings/page.tsx`, `dashboard/page.tsx`:

| Old class | New class |
|-----------|-----------|
| `bg-green-700` | `bg-[#2D6A4F]` |
| `bg-green-800` | `bg-[#1B4332]` |
| `bg-green-900` | `bg-[#1B4332]` |
| `text-green-700` | `text-[#2D6A4F]` |
| `border-green-700` | `border-[#2D6A4F]` |
| `from-green-900 to-green-700` | `from-[#1B4332] to-[#2D6A4F]` |
| `bg-gray-100` | `bg-[#F7F8F5]` |
| `border-gray-200` | `border-[#E5E7EB]` |
| `text-gray-400` | `text-[#6B7280]` |

### 4. `app/cockpit/src/app/globals.css`

Add base body style:
```css
body {
  background-color: #2D6A4F;  /* outer ring on desktop */
  font-family: 'Inter', sans-serif;
}
```

---

## Files to Touch

| File | Action |
|------|--------|
| `app/cockpit/tailwind.config.ts` | Add `pp` colour namespace |
| `app/cockpit/src/app/globals.css` | Set body background |
| `app/cockpit/src/components/ui.tsx` | Fix `Shell`, update colour classes |
| `app/cockpit/src/app/onboard/page.tsx` | Update colour classes |
| `app/cockpit/src/app/settings/page.tsx` | Update colour classes |
| `app/cockpit/src/app/dashboard/page.tsx` | Update colour classes |
| `app/cockpit/src/app/page.tsx` | Update colour classes |

---

## Acceptance Criteria

- [ ] On a 1440px desktop, content is centred at ≤390px with green sides visible
- [ ] On a 375px mobile, content fills edge to edge with no horizontal scroll
- [ ] Primary green in all buttons/headers is `#2D6A4F`, not Tailwind green-700
- [ ] Page background inside the card area is `#F7F8F5` (warm off-white), not white or gray
