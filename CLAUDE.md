# PantryPilot — Developer Context

## What This Is
AI-powered household grocery automation. Plans, confirms, and places weekly Swiggy Instamart orders using a 5-stage LLM pipeline. WhatsApp is the primary user interface for basket confirmation and editing.

---

## Repo Layout

```
app/
  cockpit/       Next.js 14 frontend (onboarding UI, basket review, dashboard)
  pilot/         FastAPI backend
    app/
      agent/     LangGraph planning pipeline (planning_graph.py)
      api/       FastAPI routers (auth, onboard, basket, orders, settings, webhooks)
      mcp/       Swiggy MCP HTTP client (swiggy.py)
      models/    SQLAlchemy ORM (db.py — 13 tables)
      providers/ LLM, WhatsApp, MCP, OTP abstractions + factory.py
      services/  Domain logic (auth, onboarding, whatsapp, household, pantry)
      tasks/     Celery tasks (planning, whatsapp, pantry, maintenance)
      schemas/   Pydantic request/response models
      utils/     logging (structlog), exceptions, crypto (AES-256-GCM)
    migrations/  Alembic schema versions
    tests/
      unit/           Service-level tests (mock everything)
      integration/    End-to-end flows using mock Swiggy MCP + in-memory SQLite
Makefile         All dev commands (see below)
```

---

## Docker Services

| Service | Port | Role |
|---|---|---|
| postgres | 5432 | Primary DB (PostgreSQL 16) |
| redis | 6379 | Cache, Celery broker/backend, OTP store |
| migrate | — | Runs Alembic on startup, exits |
| pilot | 8000 | FastAPI backend |
| pilot-worker | — | Celery worker (planning/whatsapp/pantry/maintenance queues) |
| pilot-beat | — | Celery Beat scheduler (daily token check, hourly missed-run catchup) |
| cockpit | 3000 | Next.js frontend |

---

## Make Commands

```bash
make up              # Build + start all services
make down            # Stop containers (keep DB data)
make nuke            # Destroy everything including volumes
make restart         # Rebuild + restart pilot/cockpit/worker (DB/Redis untouched)
make logs            # Tail all logs
make logs s=pilot-worker   # Tail specific service
make migrate         # Run Alembic migrations (runs inside pilot container)
make migrate-new m="desc"  # Create new migration
make test            # Run pytest suite inside pilot container
make test f=<file>   # Run single test file
make seed            # Seed 22 pantry items for latest household
make fix-dev         # Mark latest household onboarding_complete=true
make shell-pilot     # Bash shell inside pilot container
```

---

## LoopRun State Machine

```
pending → sensing → planning → optimizing → awaiting_confirmation → confirmed → placing → completed
                                                                            ↓
                                                                         skipped / failed
```

- Any stage can go to `failed` (failure_reason + failure_stage recorded)
- Empty basket → `skipped` (skip_reason: "empty_basket")
- 6-hour timeout in `awaiting_confirmation` → auto-skip
- WhatsApp "skip" → `skipped`

**Critical:** The state string `"awaiting_confirmation"` — never `"confirming"` (doesn't exist).

---

## Planning Graph Nodes (`app/agent/planning_graph.py`)

1. **sense** — Fetches pantry, applies consumption decay, gets brand prefs + recent orders. If pantry empty, bootstraps from Swiggy `your_go_to_items`. Updates state → `sensing`.
2. **plan_rules** — Deterministic rules engine. Filters by diet/allergies, selects items below reorder threshold, applies recency guard (skip items ordered <3 days ago). Updates state → `planning`.
3. **plan_llm** — Claude call. Finds category gaps, suggests up to 4 additions, validates diet compliance. Returns generic item names (no brands). Uses `ANTHROPIC_MODEL` (default: claude-sonnet-4-5).
4. **optimize** — Resolves item names to Swiggy SKUs via `search_products`. Applies brand preferences. Handles substitutions. Trims to budget. Updates state → `optimizing`.
5. **confirm** — Sends WhatsApp basket preview. Updates LoopRun → `awaiting_confirmation`. Returns immediately — does NOT wait.
6. **place** — Places order via Swiggy MCP checkout. Creates Order + OrderItem records. Updates pantry stock. Sends receipt WhatsApp. Schedules next run.

---

## API Routes (`/v1` prefix on all)

| Router | Prefix | Key Endpoints |
|---|---|---|
| auth | `/v1/auth` | POST /initiate, GET /callback, POST /logout, POST /reauth |
| onboard | `/v1/onboard` | GET /status, GET /infer, POST /profile, POST /whatsapp/send-otp, POST /whatsapp/verify-otp, GET /basket-preview, POST /complete |
| basket | `/v1/basket` | GET /pending, POST /confirm, POST /edit, POST /add-item, POST /remove-item |
| orders | `/v1/orders` | GET /recent, GET /{order_id} |
| settings | `/v1/settings` | GET /, PATCH /, POST /pause, POST /resume |
| webhooks | `/v1/webhooks` | POST /interakt, POST /twilio |

---

## Key Database Models (`app/models/db.py`)

**households** — Core user record. Fields: `swiggy_user_id`, `whatsapp_number`, `whatsapp_verified`, `household_type`, `member_count`, `diet_type`, `allergies` (ARRAY), `weekly_budget_min/max`, `onboarding_complete`, `is_active`, `is_paused`.

**household_preferences** — Scheduling + delivery prefs. Fields: `preferred_order_day`, `preferred_address_id` (FK → addresses.id, our UUID), `preferred_delivery_slot`, `next_run_at`, `last_run_at`.

**addresses** — Saved delivery addresses. Fields: `household_id`, `swiggy_address_id` (Swiggy's string ID), `label`, `is_default`. `preferred_address_id` in HouseholdPreferences is FK to `addresses.id` (our UUID), NOT to Swiggy's string ID.

**swiggy_tokens** — Encrypted OAuth tokens. Fields: `access_token_enc` (AES-256-GCM), `token_expiry`.

**pantry_items** — Stock tracking. Fields: `item_name`, `estimated_qty_remaining`, `reorder_threshold`, `avg_weekly_consumption`, consumption decay applied before each run.

**loop_runs** — Planning execution record. Tracks full state machine journey, LLM usage, timings.

**loop_run_items** — Basket contents per run. `added_by` = `rules_engine` | `llm` | `user_added`.

**orders** / **order_items** — Placed order records linked to loop_run.

---

## Address Handling — Critical Invariant

`preferred_address_id` in `HouseholdPreferences` is a UUID FK to `addresses.id` (our internal table), NOT Swiggy's string address ID (e.g. `"addr_home_001"`).

**Flow that must happen during onboarding inference (`GET /onboard/infer`):**
1. Fetch addresses from Swiggy MCP
2. Upsert into `addresses` table → get our `addr.id` (UUID)
3. Set `prefs.preferred_address_id = addr.id`

If this is skipped, the `place` node fails with "No delivery address". The `place` node has a fallback that fetches live from Swiggy if `preferred_address_id` is NULL, but the address must still be set.

---

## LLM Providers

Selected via `LLM_PROVIDER` in `.env`. Factory in `providers/factory.py`.

| Value | Provider | Notes |
|---|---|---|
| `anthropic` | Claude (default) | Requires paid credits at console.anthropic.com |
| `gemini` | Google Gemini | Requires `google-generativeai` pip package + paid quota |
| `groq` | Groq (llama/mixtral) | Free tier at console.groq.com — fast LPU inference |

---

## WhatsApp Providers

**Factory** (`providers/factory.py`): `settings.whatsapp_provider or "interakt"`. Set `WHATSAPP_PROVIDER=twilio` in `.env` for local dev.

**Twilio** (current dev setup):
- Plain text is BLOCKED by Twilio — all messages require Content Templates (ContentSid)
- Templates configured via Twilio Console → Content Template Builder
- ContentSid env vars: `TWILIO_OTP_TEMPLATE_SID`, `TWILIO_BASKET_PREVIEW_TEMPLATE_SID`
- Template variables passed as JSON via `ContentVariables` param
- Sandbox: recipient must join by sending "join <word>" to +14155238886
- Errors: code 63007 = sandbox not activated; 21608 = recipient not joined

**Interakt** (production target):
- Template-based, requires Meta approval (~2-3 days)
- Supports quick-reply buttons natively
- Requires public webhook URL (not available locally)

**Template variable mapping:**
- OTP: `{"1": otp, "2": "10"}` (code, validity minutes)
- Basket preview: `{"1": summary, "2": total, "3": budget}`

---

## Celery Tasks

| Task | Queue | Trigger |
|---|---|---|
| trigger_planning_loop | planning | Beat schedule or WhatsApp "order now" |
| place_confirmed_order | planning | WhatsApp "confirm" or UI confirm button |
| handle_confirmation_timeout | planning | Delayed 6h after confirm sent |
| add_item_to_basket | planning | WhatsApp "add milk" |
| process_inbound_message | whatsapp | Webhook (Interakt/Twilio) |
| update_pantry_from_order | pantry | After order placed |
| check_token_expiry | maintenance | Beat: 9 AM IST daily |
| catchup_missed_runs | maintenance | Beat: :05 every hour |

**Critical:** `confirm_order` WhatsApp intent must call `place_confirmed_order.delay(household_id, run_id)` — NOT `trigger_planning_loop` (which would re-plan from scratch).

---

## Swiggy MCP Tools (`app/mcp/swiggy.py`)

All calls: `POST https://mcp.swiggy.com/im` with `Authorization: Bearer {access_token}`.

Key tools: `get_addresses`, `your_go_to_items`, `get_order_history`, `search_products`, `get_cart`, `clear_cart`, `update_cart`, `checkout`, `get_order_status`.

---

## Auth Flow

1. `POST /v1/auth/initiate` → generates PKCE challenge → returns Swiggy authorize URL
2. User logs in on Swiggy, redirected to `GET /v1/auth/callback?code=...`
3. Backend exchanges code → access_token, creates/finds Household
4. Token AES-256-GCM encrypted, stored in `swiggy_tokens`
5. `household_id` set in SessionMiddleware cookie (max_age=86400)
6. Redirect to `/onboard` (new user) or `/basket` (returning user)

**New user household created with NO defaults** — `household_type`, `diet_type` etc. are NULL until onboarding profile is saved. `profile_saved` in `/onboard/status` checks `weekly_budget_max is not None`.

---

## Onboarding Steps (Frontend: `cockpit/src/app/onboard/page.tsx`)

1. Welcome
2. Household type
3. Diet type
4. Inference (GET /onboard/infer — must set preferred_address_id)
5. Phone number entry
6. OTP verify
7. Basket preview (GET /onboard/basket-preview)
8. Complete (POST /onboard/complete)

`GET /onboard/status` on mount: resumes at correct step or redirects to `/` if household not found (handles post-nuke stale cookies).

---

## Test Structure

**Integration test fixtures** (`tests/integration/conftest.py`):
- `swiggy_mcp` — patches `get_mcp_provider` with mock returning `SWIGGY_RESPONSES` dict
- `app_client` — HTTPX async test client with real FastAPI app + in-memory SQLite
- `db` — async SQLAlchemy session scoped per test

**Run tests:** `make test` (runs inside pilot container via `docker compose exec`)

**Mock Swiggy address ID in tests:** `"addr_home_001"` — used in `SWIGGY_RESPONSES["addresses"]`

---

## Known Pitfalls

1. **`preferred_address_id` must be our UUID FK**, not Swiggy's string address ID. Storing Swiggy's ID directly causes `invalid UUID` error in PostgreSQL.
2. **State string is `"awaiting_confirmation"`**, never `"confirming"`.
3. **Twilio requires Content Templates** for all WhatsApp messages — plain text is blocked.
4. **`plan_llm` fails gracefully** (returns 0 additions) if Anthropic credits are exhausted — pipeline continues but basket may be thin.
5. **New user with no Swiggy order history** → `your_go_to_items` returns empty → basket skipped. Use `make seed` to populate pantry for testing.
6. **`make migrate` runs alembic inside the pilot container** — not on Mac host (alembic not installed locally).
7. **`make nuke` destroys DB but not browser cookies** — stale cookie causes `/onboard/status` to return NOT_FOUND → frontend redirects to `/` (login page). Clear cookies manually if needed.
8. **WhatsApp `confirm_order` handler** must call `place_confirmed_order.delay()`, not `trigger_planning_loop` (re-planning from scratch is wrong).
