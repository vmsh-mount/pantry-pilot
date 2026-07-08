# PantryPilot — Task Index

> This folder tracks all features, fixes, and improvements across the project.  
> One file per task area. Update status here when tasks move.
>
> Status: ✅ Done · 🔄 In Progress · ⏳ Pending · ❌ Blocked · 🚫 Deferred

---

## Active

| ID | Title | Area | Status | File |
|----|-------|------|--------|------|
| BE-003 | Centralise external ID mapping (UUID ↔ Swiggy address ID) | Backend | ✅ Done | [be/BE-003-external-id-mapper.md](be/BE-003-external-id-mapper.md) |
| UI-015 | Basket editing — in-app review, remove, add item | Frontend + Backend | ✅ Done | [ui/UI-015-basket-editing.md](ui/UI-015-basket-editing.md) |
| UI-017 | Run visibility — schedule, history, in-progress | Frontend + Backend | ✅ Done | [ui/UI-017-run-visibility.md](ui/UI-017-run-visibility.md) |
| BE-004 | Dry run mode — order guard rail | Backend + Frontend | ✅ Done | [be/BE-004-dry-run-mode.md](be/BE-004-dry-run-mode.md) |
| BE-005 | WhatsApp enabled flag | Backend + Frontend | ✅ Done | [be/BE-005-whatsapp-enabled-flag.md](be/BE-005-whatsapp-enabled-flag.md) |
| UI-018 | Onboarding → dashboard handoff | Frontend | ✅ Done | [ui/UI-018-onboarding-dashboard-handoff.md](ui/UI-018-onboarding-dashboard-handoff.md) |
| UI-019 | Smart onboarding flow (history-aware) | Frontend | ✅ Done | [ui/UI-019-smart-onboarding-flow.md](ui/UI-019-smart-onboarding-flow.md) |
| UI-020 | Inference screen design sync ("What We Know") | Frontend | ✅ Done | [ui/UI-020-inference-screen-design-sync.md](ui/UI-020-inference-screen-design-sync.md) |

---

## Deferred

| ID | Title | Reason |
|----|-------|--------|
| UI-016 | Pantry state visualisation page | Post-MVP |

---

## Completed

| ID | Title | Completed |
|----|-------|-----------|
| UI-001 | PWA setup (manifest.json, icon.svg, layout meta) | 2026-06-28 |
| UI-002 | Layout constraint + design tokens (pp colors, Shell max-w-[390px]) | 2026-06-28 |
| UI-003 | Onboarding — segmented progress bar (StepBar) | 2026-06-28 |
| UI-004 | Onboarding — household type emoji grid (OptionGrid) | 2026-06-28 |
| UI-005 | Onboarding — diet type option list (OptionList) | 2026-06-28 |
| UI-006 | Onboarding — budget preset grid (BudgetGrid) | 2026-06-28 |
| UI-007 | Onboarding — inference summary screen | 2026-06-28 |
| UI-008 | Onboarding — 6-digit OTP boxes (OtpInput) | 2026-06-28 |
| UI-009 | Onboarding — basket preview redesign | 2026-06-28 |
| UI-010 | Onboarding — "All Set" screen | 2026-06-28 |
| UI-011 | Dashboard — items grouped by category | 2026-06-28 |
| UI-012 | Dashboard — budget bar (BudgetBar) | 2026-06-28 |
| UI-013 | Dashboard — substitution banner (SubstitutionBanner) | 2026-06-28 |
| UI-014 | Orders history page + GET /v1/orders | 2026-06-28 |
| BE-001 | `/onboard/infer` — return address_line | 2026-06-28 |
| BE-002 | Basket items — client-side category inference | 2026-06-28 |
| — | Swiggy MCP real integration | 2026-06-27 |
| — | Twilio WhatsApp OTP delivery | 2026-06-27 |
| — | Planning loop asyncio fix (NullPool / pool dispose) | 2026-06-28 |
| — | Basket API (`/v1/basket/*`) | 2026-06-28 |
| — | Dashboard page (`/dashboard`) | 2026-06-28 |
| — | Post-auth redirect → `/dashboard` | 2026-06-28 |

---

## Dependency Map

```
UI-001, UI-002          ← no deps, do first (foundation)
BE-001                  ← unblocks UI-007
BE-002                  ← unblocks UI-011
UI-003 → UI-004 → UI-005 → UI-006 → UI-007 → UI-008 → UI-009 → UI-010
UI-011 ← BE-002
UI-012, UI-013          ← parallel with UI-011
UI-014                  ← independent
UI-015                  ← depends on UI-009, BE-002
```

---

## Conventions

- **ID format:** `UI-NNN` for frontend, `BE-NNN` for backend, `INF-NNN` for infra
- **Each task file has:** Problem, Design reference, Files to touch, Acceptance criteria
- **Update this index** when a task status changes
- **New tasks** get the next available number in their category
