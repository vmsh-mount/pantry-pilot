# PantryPilot — Runbook

Everything you need to run, configure, and verify the application.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Run modes at a glance](#2-run-modes-at-a-glance)
3. [Local dev (all mocks — no API keys needed)](#3-local-dev-all-mocks)
4. [Hybrid mode (mix real + mock providers)](#4-hybrid-mode)
5. [Full production-like stack](#5-full-production-like-stack)
6. [Provider configuration reference](#6-provider-configuration-reference)
7. [Verifying the stack is healthy](#7-verifying-the-stack-is-healthy)
8. [Running tests](#8-running-tests)
9. [Exercising the API manually](#9-exercising-the-api-manually)
10. [Watching mock outputs](#10-watching-mock-outputs)
11. [Common make targets](#11-common-make-targets)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Prerequisites

| Tool | Min version | Install |
|------|-------------|---------|
| Docker Desktop | 4.x | https://docs.docker.com/desktop/ |
| Docker Compose | v2 (bundled) | included with Docker Desktop |
| Python | 3.12 | only needed to run tests outside Docker |
| Node.js | 20 | only needed to run Cockpit outside Docker |
| make | any | pre-installed on macOS/Linux |

Clone and enter the repo:
```bash
git clone <repo-url> pantry-pilot
cd pantry-pilot
```

---

## 2. Run modes at a glance

| Mode | Command | Real API keys needed? | Best for |
|------|---------|----------------------|----------|
| **Local (all mocks)** | `make up-local` | ❌ None | Daily dev, first run |
| **Hybrid** | `make up` + env overrides | ⚠️ Some | Testing a specific real provider |
| **Production-like** | `make up` + real `.env` | ✅ All | Pre-deploy validation |
| **Tests only** | `make test` | ❌ None | CI, quick feedback |
| **Mock MCP only** | `make mock-mcp` | ❌ None | Exploring MCP API shape |

---

## 3. Local dev (all mocks)

**Zero real API keys required.** All external calls are intercepted by mock providers.

### Step 1 — check your `.env`

Open `app/pilot/.env`. These lines must be set:

```env
APP_ENV=local
LLM_PROVIDER=mock
WHATSAPP_PROVIDER=mock
MCP_PROVIDER=mock
OTP_PROVIDER=mock
MOCK_MCP_BASE_URL=http://mock-mcp:8001
MOCK_MCP_PUBLIC_URL=http://localhost:8001
SWIGGY_REDIRECT_URI=http://localhost:8000/v1/auth/callback
COCKPIT_URL=http://localhost:3000
```

All other secret fields (`ANTHROPIC_API_KEY`, `INTERAKT_API_KEY`, `SWIGGY_CLIENT_ID`) can stay as `REPLACE_WITH_YOUR_*` — they won't be called.

### Step 2 — start the full stack

```bash
make up-local
```

This starts:
- `postgres` on port 5432
- `redis` on port 6379
- `migrate` (runs Alembic once, then exits)
- `mock-mcp` on port 8001 — fake Swiggy MCP server
- `pilot` on port 8000 — FastAPI backend
- `pilot-worker` — Celery worker
- `pilot-beat` — Celery scheduler
- `cockpit` on port 3000 — Next.js frontend

### Step 3 — verify everything is up

```bash
make logs          # tail all services
# or check specific services:
make logs s=pilot
make logs s=mock-mcp
```

Expected output from pilot:
```
pilot  | INFO:     Application startup complete.
pilot  | INFO:     Uvicorn running on http://0.0.0.0:8000
```

Expected output from mock-mcp:
```
mock-mcp | INFO:     Application startup complete.
mock-mcp | INFO:     Uvicorn running on http://0.0.0.0:8001
```

### Step 4 — open in browser

| URL | What |
|-----|------|
| http://localhost:3000 | Cockpit (user-facing UI) |
| http://localhost:8000/docs | Pilot Swagger UI (interactive API) |
| http://localhost:8001/docs | Mock MCP server docs + tool explorer |
| http://localhost:8000/health | Pilot health check |
| http://localhost:8001/health | Mock MCP health check |

---

## 4. Hybrid mode

Run the full stack but override **one or more** providers to use real services.

### Use real Claude (Anthropic) for LLM, keep everything else mocked

```env
# in app/pilot/.env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...your real key...
ANTHROPIC_MODEL=claude-haiku-4-5
```

Then restart:
```bash
make restart
```

### Use Gemini instead of Claude

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...your key...
GEMINI_MODEL=gemini-2.0-flash
```

### Use real WhatsApp (Interakt), keep MCP mocked

```env
WHATSAPP_PROVIDER=interakt
INTERAKT_API_KEY=your_real_key
INTERAKT_WEBHOOK_SECRET=your_real_secret
PANTRYPILOT_WHATSAPP_NUMBER=+91XXXXXXXXXX
```

### Use real Swiggy MCP, keep LLM mocked

```env
MCP_PROVIDER=swiggy
SWIGGY_MCP_BASE_URL=https://mcp.swiggy.com
SWIGGY_CLIENT_ID=your_client_id
SWIGGY_REDIRECT_URI=http://localhost:8000/v1/auth/callback
```

### Mix and match rules

- Any blank `*_PROVIDER` value defers to `APP_ENV`:
  - `APP_ENV=local` → mock
  - `APP_ENV=staging` or `production` → real
- Explicit values always win regardless of `APP_ENV`.

---

## 5. Full production-like stack

Fill in all real values in `app/pilot/.env`:

```env
APP_ENV=production
LLM_PROVIDER=anthropic       # or leave blank
WHATSAPP_PROVIDER=interakt   # or leave blank
MCP_PROVIDER=swiggy          # or leave blank
OTP_PROVIDER=redis           # or leave blank

ANTHROPIC_API_KEY=sk-ant-...
INTERAKT_API_KEY=...
INTERAKT_WEBHOOK_SECRET=...
SWIGGY_CLIENT_ID=...
SWIGGY_REDIRECT_URI=https://yourapp.com/auth/callback
```

Then:
```bash
make up
```

> **Note:** `make up` does not include the `local` Docker Compose profile, so `mock-mcp` won't start. If you set `MCP_PROVIDER=mock` without `mock-mcp` running, the pilot will fail to reach it.

---

## 6. Provider configuration reference

### Environment variables

| Variable | Values | Default (local) | Default (production) |
|----------|--------|-----------------|----------------------|
| `APP_ENV` | `local` / `staging` / `production` | `local` | — |
| `LLM_PROVIDER` | `anthropic` / `gemini` / `mock` / `""` | `mock` | `anthropic` |
| `WHATSAPP_PROVIDER` | `interakt` / `mock` / `""` | `mock` | `interakt` |
| `MCP_PROVIDER` | `swiggy` / `mock` / `""` | `mock` | `swiggy` |
| `OTP_PROVIDER` | `redis` / `mock` / `""` | `mock` | `redis` |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | not needed | required |
| `ANTHROPIC_MODEL` | any Claude model ID | `claude-sonnet-4-5` | — |
| `GEMINI_API_KEY` | `AIza...` | not needed | required if `gemini` |
| `GEMINI_MODEL` | any Gemini model ID | `gemini-2.0-flash` | — |
| `INTERAKT_API_KEY` | Interakt key | not needed | required |
| `INTERAKT_WEBHOOK_SECRET` | Interakt secret | not needed | required |
| `SWIGGY_CLIENT_ID` | from Builders Club | not needed | required |
| `SWIGGY_REDIRECT_URI` | OAuth callback — **must point to pilot** | `http://localhost:8000/v1/auth/callback` | `https://api.yourapp.com/v1/auth/callback` |
| `COCKPIT_URL` | Frontend base URL for post-auth redirects | `http://localhost:3000` | `https://yourapp.com` |
| `MOCK_MCP_BASE_URL` | Docker-internal mock MCP URL (server→server) | `http://mock-mcp:8001` | — |
| `MOCK_MCP_PUBLIC_URL` | Browser-accessible mock MCP URL (OAuth redirect) | `http://localhost:8001` | — |

### What each mock does

| Mock | Observable behaviour |
|------|---------------------|
| **Mock LLM** | Returns 3 scripted basket additions (turmeric, green tea, oats). Logs `[MOCK LLM] complete() called` |
| **Mock WhatsApp** | Prints every outbound message to stdout: `[MOCK WA] OTP → +91...: 123456` |
| **Mock MCP** | In-process. 40 Indian grocery products, 5 past orders, stateful cart. Checkout generates `MOCK-XXXXXXXX` order IDs |
| **Mock OTP** | Prints OTP to stdout: `[MOCK OTP] *** OTP for +91...: 123456 ***`. In-memory store. |

### Mock MCP HTTP server (separate from in-process mock)

When `make up-local` is used, the `mock-mcp` Docker service also runs. It implements the same Swiggy JSON-RPC wire format over HTTP. The in-process `MockMCPProvider` and the HTTP mock server are **independent** — the in-process one is used by default (no HTTP hop). The HTTP server is useful for:
- Exploring the MCP API shape via `/docs`
- Testing from outside Docker
- Pointing any MCP-compatible client at it

Endpoints:
```
POST http://localhost:8001/im          # tool calls (JSON-RPC)
GET  http://localhost:8001/auth/authorize?redirect_uri=...&state=...
POST http://localhost:8001/auth/token
POST http://localhost:8001/auth/logout
GET  http://localhost:8001/health
GET  http://localhost:8001/docs
```

---

## 7. Verifying the stack is healthy

### Quick health check
```bash
curl http://localhost:8000/health
# → {"status": "ok"}

curl http://localhost:8001/health
# → {"status": "ok", "service": "mock-swiggy-mcp", "version": "1.0.0"}

curl http://localhost:3000
# → HTML (Cockpit landing)
```

### Check Docker service status
```bash
cd app && docker compose --profile local ps
```

All services should show `Up` or `healthy`. The `migrate` service will show `Exit 0` — that's correct.

### Check Celery worker is processing
```bash
make logs s=pilot-worker
# Look for: [tasks.planning] ready
```

### Database sanity check (optional)
```bash
make shell-pilot
# inside the container:
python -c "from app.db import engine; print('DB OK')"
```

---

## 8. Running tests

Tests never hit real APIs — the `conftest.py` fixture auto-mocks all providers.

### Run the full suite
```bash
make test
# equivalent to: cd app/pilot && python -m pytest
```

### Run a single test file
```bash
make test f=tests/test_pantry_service.py
make test f=tests/test_planning_service.py
make test f=tests/test_whatsapp_service.py
make test f=tests/test_onboarding_service.py
make test f=tests/test_household_service.py
```

### Run a single test by name
```bash
cd app/pilot && python -m pytest tests/test_pantry_service.py::test_decay_reduces_quantity -v
```

### Watch mode (re-runs on file save)
```bash
make test-watch
# requires: pip install pytest-watcher
```

### What the tests cover

| File | Tests | Coverage |
|------|-------|----------|
| `test_onboarding_service.py` | 16 | OTP lifecycle, inference, preview basket |
| `test_whatsapp_service.py` | 35 | Intent classification, payload parsing, template correctness |
| `test_pantry_service.py` | 27 | Decay model, EMA, reorder logic |
| `test_planning_service.py` | 15 | Diet filters, LLM safety net, service guards |
| `test_household_service.py` | 14 | Settings, pause/resume, DPDP delete |
| **Total** | **107** | |

---

## 9. Exercising the API manually

The Swagger UI at http://localhost:8000/docs has every endpoint. For curl:

### Check API health
```bash
curl http://localhost:8000/health
```

### Start Swiggy OAuth (mock flow — redirects immediately)
```bash
curl -v "http://localhost:8000/v1/auth/initiate?session_id=test-session-123"
# Returns a redirect URL. In local mode, the mock MCP auth server at :8001
# immediately redirects back with a fake code — no Swiggy account needed.
```

### Simulate a WhatsApp webhook (inbound message)
```bash
curl -X POST http://localhost:8000/v1/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -H "X-Interakt-Signature: mock_signature" \
  -d '{
    "type": "message",
    "data": {
      "message": {
        "type": "text",
        "text": "yes confirm"
      },
      "customer": {
        "phone_number": "+919876543210"
      }
    }
  }'
```

> In local mode, signature validation uses `settings.interakt_webhook_secret = "dev_webhook_secret"`. You can bypass it in tests via the mock fixture.

### Trigger the planning loop manually (Celery task)
```bash
make shell-pilot
# inside container:
python -c "
from app.tasks.planning import run_planning_loop
run_planning_loop.delay('your-household-uuid')
"
```

### Hit the mock MCP server directly
```bash
# Search products
curl -X POST http://localhost:8001/im \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer mock.token" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "search_products",
      "arguments": {"query": "dal", "address_id": "addr_001", "limit": 5}
    },
    "id": 1
  }'

# Get order history
curl -X POST http://localhost:8001/im \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer mock.token" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {"name": "get_orders", "arguments": {"limit": 5}},
    "id": 2
  }'

# Get addresses
curl -X POST http://localhost:8001/im \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer mock.token" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {"name": "get_addresses", "arguments": {}},
    "id": 3
  }'
```

### Generate fresh secrets
```bash
make gen-keys
# Prints new TOKEN_ENCRYPTION_KEY, JWT_SECRET, INTERNAL_API_SECRET
```

---

## 10. Watching mock outputs

Since real notifications are replaced by console logs, tail the pilot logs to see what would normally go to WhatsApp, Claude, etc.:

```bash
make logs s=pilot
```

Look for these prefixes:

```
[MOCK WA]  OTP → +91...: 123456              ← WhatsApp OTP
[MOCK WA]  basket_preview → +91...            ← Basket sent to user
[MOCK WA]  order_receipt → +91...             ← Order confirmed
[MOCK LLM] complete() called                  ← LLM gap-fill triggered
[MOCK MCP] search_products(query='dal')       ← Instamart search
[MOCK MCP] checkout(address_id='addr_001')    ← Order placed
[MOCK OTP] *** OTP for +91...: 123456 ***    ← OTP generated
```

To see only mock events:
```bash
cd app && docker compose --profile local logs -f pilot | grep "\[MOCK"
```

---

## 11. Common make targets

```bash
# Stack
make up-local        # Start full stack with all mocks (includes mock-mcp)
make up              # Start full stack (production mode, no mock-mcp)
make down            # Stop and remove all containers
make nuke            # ⚠️  Destroy everything — containers + volumes + DB data
make restart         # down + up
make mock-mcp        # Start only the mock Swiggy MCP server

# Logs
make logs            # Tail all services
make logs s=pilot    # Tail a specific service (pilot / cockpit / mock-mcp / etc.)

# Database
make migrate         # Apply pending Alembic migrations
make migrate-new m="add user table"   # Create a new migration
make migrate-down    # Rollback one migration

# Testing
make test            # Run full pytest suite (107 tests)
make test f=tests/test_pantry_service.py   # Single file
make test-watch      # Re-run on file change

# Code quality
make lint            # ruff + mypy on pilot

# Dev setup
make install-pilot   # pip install -r requirements.txt
make install-cockpit # npm install
make gen-keys        # Print fresh secret values

# Cleanup
make clean           # Remove __pycache__, .next, pytest caches
```

---

## 12. Troubleshooting

### `pilot` fails to start — "connection refused" to postgres/redis
The migrate service may still be running. Wait 10–15 seconds and retry, or:
```bash
cd app && docker compose --profile local logs migrate
```
If migration failed, check `DATABASE_URL` in `.env`.

### Mock MCP not found — `Connection refused localhost:8001`
You started with `make up` (no `--profile local`). Use `make up-local` or:
```bash
make mock-mcp   # starts mock-mcp separately
```

### OTP never arrives (local mode)
That's expected. The OTP is printed to the pilot logs instead:
```bash
make logs s=pilot | grep "MOCK OTP"
```

### WhatsApp messages not arriving (local mode)
Same — look in logs:
```bash
make logs s=pilot | grep "MOCK WA"
```

### Tests failing with import errors
Make sure you're running from the right directory:
```bash
cd app/pilot && python -m pytest   # correct
# OR
make test                           # always correct
```

### `LLM_PROVIDER=gemini` fails with ImportError
Install the Gemini SDK:
```bash
cd app/pilot && pip install google-generativeai
```

### Switching from mock to real provider mid-session
Edit `.env`, then force-recreate pilot so the new env is picked up:
```bash
cd app && docker compose --profile local up -d --force-recreate pilot pilot-worker
```
> `restart` reuses the old env snapshot. `--force-recreate` re-reads the `env_file`.

### Stuck after OAuth — browser lands on `localhost:8000/...` instead of `localhost:3000/...`
Check these two values in `app/pilot/.env`:
```env
SWIGGY_REDIRECT_URI=http://localhost:8000/v1/auth/callback   # ← must be pilot, not cockpit
COCKPIT_URL=http://localhost:3000                             # ← pilot redirects here after auth
```
Then force-recreate pilot.

### Reset everything to a clean slate
```bash
make nuke        # destroys all containers, volumes, and DB data
make up-local    # fresh start — migrations run automatically
```
