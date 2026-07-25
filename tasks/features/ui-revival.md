# PRD — UI Revival: Shared Shell + Bottom Nav

**Status:** Ready for implementation
**Date:** 2026-07-25
**Branch:** `feature/ui-revival-shell`
**Mockup:** [`tasks/features/ui-revival-mockup.html`](./ui-revival-mockup.html) — layout **Option B** approved

---

## Problem

The app ships two competing layout patterns, so moving between screens doesn't feel like one product:

- **Two-section** (green hero + light-gray body) — Dashboard, Pantry. Polished.
- **Full-green `AppShell`** (white cards floating on a full-viewport `#2D6A4F`) — Orders, Settings, Routines, Flow, Quick, Runs, Nutrition. The "out of place" screens.

The inconsistency and its symptoms all trace to three shared primitives in [`components/ui.tsx`](../../app/cockpit/src/components/ui.tsx):

| User feedback | Root cause |
|---|---|
| #1 Header is full-bleed green | `AppShell` / `Shell` wrap everything in `bg-[#2D6A4F]` |
| #2 No consistent look & feel | Two layout patterns coexist; no single shell |
| #3 Settings tab redundant | *(Deferred — keeping 5 tabs per decision)* |
| #4 Bottom icons childish / off-theme | `BottomNav` uses emoji (🏠🥫📦🔄⚙️) |
| #5 Whole-page green, jarring transitions | Full-green `AppShell` on half the screens |
| #6 Buttons faded into background | `Button` `secondary` variant is `bg-[#F7F8F5]` (near-white) |
| #7 Back icon + "Back" word redundant | Three different back conventions across pages |

---

## Goals

- One canonical page shell — **Option B**: green hero header + light-gray scrollable body + persistent bottom nav.
- Redesigned bottom nav: **5 tabs kept**, emoji replaced with a standard stroke-based line-icon set, clearer active state.
- Fixed shared `Button` (secondary no longer washed out) and a single `BackButton` control.
- Migrate the **five tab screens** onto the shell so every primary destination is consistent.

## Non-Goals (explicitly deferred to a later task)

- Secondary screens: Flow, Quick, Runs, Routine detail/new/edit, Nutrition (gaps/weekly), Reauth. They keep `AppShell` for now.
- Onboarding / Auth flows — the full-green `Shell` there is intentional; leave it.
- Removing the Settings tab (decision: keep 5 tabs).
- Dark mode, new features, any backend change.

---

## Design Tokens

Centralize the values currently hard-coded per page. Add to a shared module (`lib/theme.ts` or exported consts in `ui.tsx`):

```
green        #2D6A4F   hero, primary
green-dark   #1B4332   pressed
pale-green   #D8F3DC   active-tab pill, secondary tint
orange       #F4845F   warnings / over-target
bg           #F4F4F4   gray body
card         #FFFFFF
hairline     rgba(0,0,0,0.07)
ink / 2 / 3  #1C1C1E / #5A5A5F / #8E8E93
```

Card default: `bg-card`, `border 1px hairline`, `rounded-[14px]`, **no drop shadow** in the gray body (shadow was only needed to lift cards off green — flat reads cleaner on gray).

---

## New / Changed Shared Components

All in [`components/ui.tsx`](../../app/cockpit/src/components/ui.tsx) unless noted.

### 1. `PageShell` (new) — the canonical layout

```tsx
<PageShell hero={<PageHero title="Order History" subtitle="7 orders · ₹8,420 this month" />}>
  {/* body content, rendered in the gray section */}
</PageShell>
```

Structure it renders:
- `<main>` full height, `bg-[#F4F4F4]`, column, centered, `max-w-[390px]` inner column.
- **Green hero** (`bg-[#2D6A4F]`) at top — wraps the `hero` slot; full-width green but only as tall as its content (fixes #1: green no longer fills the viewport).
- **Gray body** — `flex-1`, `bg-[#F4F4F4]`, padded, holds `children`, bottom padding `pb-28` to clear the nav.
- Renders `<BottomNav/>` unless `showNav={false}`.

Props: `hero: ReactNode`, `children: ReactNode`, `showNav?: boolean = true`.

### 2. `PageHero` (new) — standard hero content

Two modes so the rich data heroes and the simple list heroes share one grammar:
- **Simple mode** — `<PageHero title subtitle />` — brand bar (🥦 PantryPilot + settings gear top-right) + `<h2>` title + optional subtitle. Used by **Orders, Settings, Routines**.
- **Custom-slot mode** — the page passes its own hero content through `PageShell`'s `hero` slot (brand bar still provided by the shell). Used by **Dashboard** (spend stat, alerts) and **Pantry** (item count + running-low/depleted stats footer). These heroes carry real data and must **not** be collapsed into a generic subtitle string — preserve the existing layout, just move it into the shell's `hero` slot instead of a hand-rolled green wrapper.

The settings gear in the brand bar links to `/settings` (it's an entry point, not a replacement for the tab). **Omit the gear on the Settings page itself** — a self-referencing link is redundant.

### 3. `BottomNav` (redesign)

- Keep the 5 tabs: Home, Pantry, Orders, Routines, Settings.
- Replace emoji with inline SVG line icons (24×24, `stroke-width 2`, round caps). Icon set lives in a new `components/icons.tsx` (see below).
- Active tab: pale-green pill behind the icon + `text-[#2D6A4F]` label. Inactive: `text-[#8E8E93]`.
- Keep the floating rounded-`2xl` white bar, `max-w-[390px]`, safe-area bottom padding.

### 4. `icons.tsx` (new) — shared line-icon set

Single source for stroke icons. Ship what this scope needs:
`home, pantry, orders, routines, settings, chevronRight, back`.
Each is a small function component `({size=20, className}) => <svg …/>`. (Dashboard's existing ad-hoc `IconFlame`/`IconRefresh`/etc. can migrate here later — out of scope now, but put new icons here so there's one home.)

### 5. `Button` (fix `secondary`)

```
secondary: "bg-white text-[#2D6A4F] border-1.5 border-[#2D6A4F] hover:bg-[#D8F3DC]"
```
Solid outline instead of `bg-[#F7F8F5]` (fixes #6). `primary`, `ghost`, `danger` unchanged.

### 6. `BackButton` (new)

One icon-only control (fixes #7): a 30×30 rounded tap target with the `back` chevron. Variant prop for placement context — `on-green` (translucent white bg, white icon) for hero, default (subtle gray bg, ink icon) for light. The page **title carries the label** — no "Back" word.

---

## Page Migrations (in scope)

| Page | Current | Change |
|---|---|---|
| `dashboard/page.tsx` | Inline two-section, **custom hero** | Wrap in `PageShell`; move existing hero (spend stat, alerts) into the `hero` slot. Visual result unchanged. |
| `pantry/page.tsx` | Inline two-section, **custom hero** | Same — `PageShell` with custom-slot hero. **Keep** the item-count + running-low/depleted stats footer; do not flatten to a subtitle. |
| `orders/page.tsx` | `AppShell` (full green) | → `PageShell` + `PageHero("Order History")`. Cards become flat gray-body cards (remove float shadow). |
| `routines/page.tsx` | `AppShell` | → `PageShell` + `PageHero("Routines")`. |
| `settings/page.tsx` | `AppShell` | → `PageShell` + `PageHero("Settings")`. |

`AppShell` stays in `ui.tsx` (still used by out-of-scope pages) but is no longer imported by any of the five above. Add a `// deprecated — migrate to PageShell` comment on it.

---

## Acceptance Criteria

- All five tab screens render green-hero + gray-body; none show full-viewport green.
- Bottom nav shows line icons on every screen; active tab has the pale-green pill; Settings is still tab #5.
- Navigating Home → Orders → Settings → Routines → Pantry feels continuous — same hero grammar, same body, nav never jumps.
- No `secondary` button appears washed-out; outline is clearly visible on white and gray.
- Every in-scope page with a back affordance uses the single `BackButton`; no "← Back" text remains on them.
- No `bg-[#2D6A4F]` full-page wrapper remains in the five migrated pages (green only in the hero).
- `npx tsc --noEmit` clean for changed files; no new console errors in the preview.

---

## Files to Change

| File | Change |
|---|---|
| `app/cockpit/src/components/ui.tsx` | Add `PageShell`, `PageHero`, `BackButton`; redesign `BottomNav`; fix `Button` secondary; deprecate `AppShell` |
| `app/cockpit/src/components/icons.tsx` | NEW — shared line-icon set |
| `app/cockpit/src/lib/theme.ts` | NEW (optional) — token constants |
| `app/cockpit/src/app/dashboard/page.tsx` | Adopt `PageShell` |
| `app/cockpit/src/app/pantry/page.tsx` | Adopt `PageShell` |
| `app/cockpit/src/app/orders/page.tsx` | `AppShell` → `PageShell` + `PageHero`; flatten cards |
| `app/cockpit/src/app/routines/page.tsx` | `AppShell` → `PageShell` + `PageHero` |
| `app/cockpit/src/app/settings/page.tsx` | `AppShell` → `PageShell` + `PageHero` |

No backend changes. No new dependencies (icons are inline SVG).

---

## Verification

Run the dev app (Docker cockpit already serves `localhost:3000`), then in the preview browser walk all five tabs and confirm the acceptance criteria — screenshot Home, Orders, Settings for the before/after record.
