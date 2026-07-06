# PantryPilot — Project Tracker

> Last updated: 2026-06-28
> Status legend: ✅ Done · 🔄 In Progress · ⏳ Pending · ❌ Blocked

---

## Phase 1 — Core Backend

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1.1 | Project scaffold (FastAPI, Celery, SQLAlchemy, Redis) | ✅ Done | `app/pilot/` |
| 1.2 | Config & settings (`app/config.py`, `.env`) | ✅ Done | Pydantic Settings v2 |
| 1.3 | Database models (13 tables) | ✅ Done | `app/models/db.py` |
| 1.4 | Alembic migrations (`0001_initial_schema`) | ✅ Done | Async-compatible via `NullPool` |
| 1.5 | Auth service (Swiggy OAuth 2.1 + PKCE) | ✅ Done | `app/services/auth_service.py` |
| 1.6 | Swiggy MCP client (13 Instamart tools) | ✅ Done | `app/mcp/swiggy.py` |
| 1.7 | Token encryption (AES-256-GCM) | ✅ Done | `app/utils/crypto.py` |
| 1.8 | JWT middleware | ✅ Done | `app/middleware/auth.py` |
| 1.9 | API routers (auth, onboarding, settings, webhook) | ✅ Done | `app/api/` |

---

## Phase 2 — Services

| # | Task | Status | Notes |
|---|------|--------|-------|
| 2.1 | Onboarding service (inference, OTP, preview basket) | ✅ Done | `app/services/onboarding_service.py` |
| 2.2 | WhatsApp service (Interakt BSP, 6 templates) | ✅ Done | `app/services/whatsapp_service.py` |
| 2.3 | Pantry service (decay model, EMA, reorder logic) | ✅ Done | `app/services/pantry_service.py` |
| 2.4 | Planning service (loop orchestration, reschedule) | ✅ Done | `app/services/planning_service.py` |
| 2.5 | Household service (settings, pause, resume, delete) | ✅ Done | `app/services/household_service.py` |

---

## Phase 3 — Planning Agent

| # | Task | Status | Notes |
|---|------|--------|-------|
| 3.1 | LangGraph `PlanningState` TypedDict | ✅ Done | `app/agent/state.py` |
| 3.2 | `sense` node (pantry decay + reorder candidates) | ✅ Done | `app/agent/planning_graph.py` |
| 3.3 | `plan_rules` node (diet filters, thresholds) | ✅ Done | |
| 3.4 | `plan_llm` node (Claude Haiku gap-fill + safety net) | ✅ Done | |
| 3.5 | `optimize` node (budget trim, dedup, sort) | ✅ Done | |
| 3.6 | `confirm` node (WhatsApp message + DB persist) | ✅ Done | |
| 3.7 | `place` node (Instamart checkout via MCP) | ✅ Done | Triggered externally after user confirms |
| 3.8 | Conditional abort edges (token expired, empty basket) | ✅ Done | |

---

## Phase 4 — Celery Tasks

| # | Task | Status | Notes |
|---|------|--------|-------|
| 4.1 | `tasks/planning.py` — run loop, place order, handle timeout | ✅ Done | |
| 4.2 | `tasks/whatsapp.py` — intent dispatcher (10 intents) | ✅ Done | |
| 4.3 | `tasks/pantry.py` — bootstrap, decay sweep, post-order update | ✅ Done | |
| 4.4 | `tasks/maintenance.py` — missed runs, reauth reminders | ✅ Done | |
| 4.5 | `tasks/celery_app.py` — Beat schedule (daily/hourly) | ✅ Done | 4 queues |

---

## Phase 5 — Frontend (Cockpit)

| # | Task | Status | Notes |
|---|------|--------|-------|
| 5.1 | Next.js 15 project scaffold + Tailwind config | ✅ Done | `app/cockpit/` |
| 5.2 | Shared UI primitives (`ui.tsx`) | ✅ Done | 12 components |
| 5.3 | API client (`lib/api.ts`) | ✅ Done | Typed wrappers for all endpoints |
| 5.4 | Onboarding wizard (`/onboard`, 4 steps) | ✅ Done | Inference pre-fill, OTP, basket preview |
| 5.5 | Placing page (`/onboard/placing`) | ✅ Done | Animated spinner, auto-redirect |
| 5.6 | Done page (`/onboard/done`) | ✅ Done | |
| 5.7 | Re-auth page (`/reauth`) | ✅ Done | Success branch + auto-redirect |
| 5.8 | Settings page (`/settings`) | ✅ Done | Auto-save, pause/resume/delete dialogs |

---

## Phase 6 — Infrastructure & Dev Setup

| # | Task | Status | Notes |
|---|------|--------|-------|
| 6.1 | `docker-compose.yml` (postgres, redis, migrate, pilot, cockpit) | ✅ Done | `migrate` runs once before pilot starts |
| 6.2 | `app/cockpit/Dockerfile` (3-stage production) | ✅ Done | |
| 6.3 | `app/cockpit/Dockerfile.dev` (hot-reload) | ✅ Done | |
| 6.4 | `app/pilot/.env` + `.env.example` | ✅ Done | |
| 6.5 | `app/cockpit/.env.local` | ✅ Done | |
| 6.6 | Root `Makefile` (up, test, migrate, gen-keys, clean, …) | ✅ Done | |
| 6.7 | `pytest.ini` + `tests/conftest.py` | ✅ Done | `asyncio_mode=auto`, autouse mocks |

---

## Phase 7 — Tests

| # | Task | Status | Notes |
|---|------|--------|-------|
| 7.1 | `test_onboarding_service.py` (16 tests) | ✅ Done | |
| 7.2 | `test_whatsapp_service.py` (35 tests) | ✅ Done | |
| 7.3 | `test_pantry_service.py` (27 tests) | ✅ Done | |
| 7.4 | `test_planning_service.py` (15 tests) | ✅ Done | |
| 7.5 | `test_household_service.py` (14 tests) | ✅ Done | |
| 7.6 | Router/API integration tests | ⏳ Pending | |
| 7.7 | LangGraph end-to-end tests (full graph run) | ⏳ Pending | |
| 7.8 | MCP client tests (mock Swiggy HTTP responses) | ⏳ Pending | |

---

## Phase 8 — UI Gaps

> Tracked in [`tasks/INDEX.md`](tasks/INDEX.md) with individual task files under `tasks/ui/` and `tasks/be/`.  
> Update status in the task index, not here.

| ID | Title | Status |
|----|-------|--------|
| UI-001 | PWA setup | ⏳ Pending |
| UI-002 | Layout constraint + design tokens | ⏳ Pending |
| UI-003–010 | Onboarding redesign (8 sub-tasks) | ⏳ Pending |
| UI-011–013 | Dashboard improvements | ⏳ Pending |
| UI-014 | Orders history page | ⏳ Pending |
| BE-001 | `/onboard/infer` return address line | ⏳ Pending |
| BE-002 | Basket items persist category field | ⏳ Pending |

---

## Phase 9 — Other Pending / Upcoming Work

| # | Task | Status | Priority | Notes |
|---|------|--------|----------|-------|
| 8.1 | Swiggy OAuth credential registration (Builders Club) | ⏳ Pending | 🔴 High | Blocking production |
| 8.2 | Interakt WhatsApp template registration (6 templates) | ⏳ Pending | 🔴 High | Blocking production |
| 8.3 | Error handling & retry strategy review (MCP calls) | ⏳ Pending | 🟠 Medium | Exponential backoff |
| 8.4 | Observability — Sentry DSN wiring + structured logging | ⏳ Pending | 🟠 Medium | `sentry_dsn` in settings is wired, not initialised |
| 8.5 | Rate limiting on public endpoints (webhook, auth) | ⏳ Pending | 🟠 Medium | Prevent abuse |
| 8.6 | Cockpit — basket edit page (add/remove items inline) | ⏳ Pending | 🟡 Low | Post-MVP |
| 8.7 | Cockpit — order history page | ⏳ Pending | 🟡 Low | Post-MVP |
| 8.8 | Cockpit — pantry state visualisation | ⏳ Pending | 🟡 Low | Post-MVP |
| 8.9 | AWS deployment (ECS / EC2 + RDS + ElastiCache) | ⏳ Pending | 🟠 Medium | Infra TBD |
| 8.10 | CI/CD pipeline (GitHub Actions) | ⏳ Pending | 🟠 Medium | `make test` + Docker build |
| 8.11 | Multi-household support (admin view) | ⏳ Pending | 🟡 Low | Post-MVP |

---

## Design Decisions to Revisit

| # | Topic | Decision Made | Open Questions |
|---|-------|--------------|----------------|
| D1 | Swiggy token refresh | No refresh tokens — re-auth via WhatsApp link after 5 days | How to make re-auth frictionless? Deep-link back into OAuth flow |
| D2 | Planning confirmation window | 6-hour timeout → auto-confirm | Should timeout be configurable per household? |
| D3 | Budget max enforcement | Hard trim (packaged first, staples last) | Should user be warned items were trimmed? |
| D4 | LLM model for planning | `claude-haiku-4-5` (fast + cheap) | Upgrade path to Sonnet for complex diets? |
| D5 | Pantry decay on non-staples | Decay applied uniformly | Fresh produce decays faster — separate model needed? |
| D6 | WhatsApp as sole UI channel | All confirmations via WA | Web-based confirm fallback for low-signal users? |
| D7 | Celery queue isolation | 4 queues: planning, pantry, whatsapp, maintenance | Queue depth alerting / worker auto-scaling strategy? |

---

## Notes

- All backend paths relative to `app/pilot/`
- All frontend paths relative to `app/cockpit/`
- Run `make gen-keys` to generate fresh `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET`, `INTERNAL_API_SECRET`
- `make up` starts the full stack; migrations run automatically before pilot starts
