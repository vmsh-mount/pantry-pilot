# PantryPilot — Low-Level Design
*Last updated: 2026-06-26*

---

## Table of Contents

1. [Database Schema](#1-database-schema)
2. [API Contracts](#2-api-contracts)
3. [Service Interfaces](#3-service-interfaces)
4. [LangGraph Agent Definition](#4-langgraph-agent-definition)
5. [Celery Task Definitions](#5-celery-task-definitions)
6. [MCP Client Wrapper](#6-mcp-client-wrapper)
7. [Redis Key Design](#7-redis-key-design)
8. [Error Handling](#8-error-handling)
9. [Environment Variables](#9-environment-variables)

---

## 1. Database Schema

### Overview of Tables

| Table | Purpose |
|---|---|
| `households` | Core household record — one per user |
| `household_preferences` | Delivery window, order schedule, frequency |
| `household_members` | Individual member profiles (V2 nutrition — schema ready) |
| `swiggy_tokens` | Encrypted Swiggy OAuth tokens |
| `addresses` | Saved delivery addresses (Swiggy address IDs) |
| `brand_preferences` | Per-household brand preferences per category |
| `pantry_items` | Pantry state per household |
| `loop_runs` | Planning loop execution records |
| `loop_run_items` | Basket items for each loop run |
| `loop_run_edits` | User edits made during confirmation |
| `orders` | Successfully placed Swiggy orders |
| `order_items` | Line items for each placed order |
| `whatsapp_conversations` | Active conversation state per household |

---

### 1.1 households

```sql
CREATE TABLE households (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity
    swiggy_user_id      TEXT UNIQUE NOT NULL,       -- Swiggy's internal user ID
    whatsapp_number     TEXT,                        -- E.164 format: +919876543210
    whatsapp_verified   BOOLEAN DEFAULT FALSE,
    whatsapp_opted_out  BOOLEAN DEFAULT FALSE,

    -- Profile (from onboarding questionnaire)
    household_type      TEXT NOT NULL,               -- solo | couple | family | joint_family
    member_count        INTEGER NOT NULL DEFAULT 1,
    diet_type           TEXT NOT NULL,               -- vegetarian | vegan | jain
    allergies           TEXT[] DEFAULT '{}',         -- ['lactose', 'gluten', 'nuts']
    weekly_budget_min   INTEGER,                     -- INR
    weekly_budget_max   INTEGER,                     -- INR

    -- Status
    onboarding_complete BOOLEAN DEFAULT FALSE,
    is_active           BOOLEAN DEFAULT TRUE,
    is_paused           BOOLEAN DEFAULT FALSE,
    paused_at           TIMESTAMPTZ,
    paused_reason       TEXT,

    -- Metadata
    city                TEXT DEFAULT 'Bengaluru',
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_households_swiggy_user   ON households(swiggy_user_id);
CREATE INDEX idx_households_whatsapp      ON households(whatsapp_number);
CREATE INDEX idx_households_active        ON households(is_active, is_paused);
```

---

### 1.2 household_preferences

```sql
CREATE TABLE household_preferences (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id            UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,

    -- Order schedule
    preferred_order_day     TEXT NOT NULL DEFAULT 'sunday',
    -- monday | tuesday | wednesday | thursday | friday | saturday | sunday
    preferred_order_time    TIME NOT NULL DEFAULT '10:00:00',
    -- Local IST time

    -- Per-category frequency (V1: all weekly, V1.5: user-configurable)
    freq_staples            TEXT NOT NULL DEFAULT 'weekly',
    freq_fresh_produce      TEXT NOT NULL DEFAULT 'weekly',
    freq_dairy_eggs         TEXT NOT NULL DEFAULT 'weekly',
    freq_packaged           TEXT NOT NULL DEFAULT 'weekly',
    -- Allowed values: daily | every_2_days | every_3_days | weekly | fortnightly

    -- Delivery preferences
    preferred_address_id    UUID REFERENCES addresses(id),
    preferred_delivery_slot TEXT DEFAULT 'evening',
    -- morning | afternoon | evening | night

    -- Confirmation window
    confirmation_window_hrs INTEGER NOT NULL DEFAULT 4,

    -- Scheduler state
    next_run_at             TIMESTAMPTZ,
    last_run_at             TIMESTAMPTZ,

    created_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now(),

    UNIQUE(household_id)
);

CREATE INDEX idx_preferences_next_run ON household_preferences(next_run_at)
    WHERE next_run_at IS NOT NULL;
```

---

### 1.3 household_members

```sql
-- V1: created but minimally populated (member_count only)
-- V2: fully populated for nutrition engine

CREATE TABLE household_members (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id    UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,

    -- Basic (V1)
    role            TEXT,           -- adult | child | elderly | infant
    diet_override   TEXT,           -- NULL = inherits household diet_type

    -- Nutrition inputs (V2)
    age_years       INTEGER,
    sex             TEXT,           -- male | female | other
    weight_kg       DECIMAL(5,1),
    height_cm       DECIMAL(5,1),
    activity_level  TEXT,           -- sedentary | light | moderate | active | very_active
    health_flags    TEXT[] DEFAULT '{}',
    -- ['diabetic', 'hypertensive', 'lactose_intolerant', 'pregnant']

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_members_household ON household_members(household_id);
```

---

### 1.4 swiggy_tokens

```sql
CREATE TABLE swiggy_tokens (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id        UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,

    -- Token (encrypted AES-256 at application layer before insert)
    access_token_enc    TEXT NOT NULL,              -- encrypted ciphertext
    token_expiry        TIMESTAMPTZ NOT NULL,       -- now() + 5 days at insert

    -- Re-auth nudge state
    nudge_48hr_sent     BOOLEAN DEFAULT FALSE,
    nudge_24hr_sent     BOOLEAN DEFAULT FALSE,
    nudge_expired_sent  BOOLEAN DEFAULT FALSE,

    -- Audit
    created_at          TIMESTAMPTZ DEFAULT now(),
    last_used_at        TIMESTAMPTZ,

    UNIQUE(household_id)                            -- one active token per household
);

CREATE INDEX idx_tokens_expiry ON swiggy_tokens(token_expiry);
-- Used by daily expiry check job
```

---

### 1.5 addresses

```sql
CREATE TABLE addresses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id        UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,

    swiggy_address_id   TEXT NOT NULL,              -- Swiggy's address ID from get_addresses
    label               TEXT,                       -- "Home", "Office" etc.
    area                TEXT,                       -- "Koramangala"
    city                TEXT DEFAULT 'Bengaluru',
    is_default          BOOLEAN DEFAULT FALSE,

    created_at          TIMESTAMPTZ DEFAULT now(),

    UNIQUE(household_id, swiggy_address_id)
);

CREATE INDEX idx_addresses_household ON addresses(household_id);
```

---

### 1.6 brand_preferences

```sql
CREATE TABLE brand_preferences (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id    UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,

    category        TEXT NOT NULL,      -- staples | fresh_produce | dairy | packaged
    item_name       TEXT NOT NULL,      -- generic item: "toor dal", "atta", "butter"
    preferred_brand TEXT NOT NULL,      -- "Tata Sampann", "Aashirvaad", "Amul"
    confidence      DECIMAL(3,2)        -- 0.0–1.0, based on order history frequency

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    UNIQUE(household_id, item_name)
);

CREATE INDEX idx_brand_prefs_household ON brand_preferences(household_id);
```

---

### 1.7 pantry_items

```sql
CREATE TABLE pantry_items (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id            UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,

    -- Item identity
    item_name               TEXT NOT NULL,      -- "Toor Dal"
    category                TEXT NOT NULL,
    -- staples | fresh_produce | dairy_eggs | packaged
    standard_unit           TEXT NOT NULL,      -- kg | litre | pieces | grams

    -- Stock tracking
    last_ordered_qty        DECIMAL(8,3),
    last_ordered_at         TIMESTAMPTZ,
    estimated_qty_remaining DECIMAL(8,3) DEFAULT 0,
    reorder_threshold       DECIMAL(8,3) NOT NULL,

    -- Consumption model
    avg_weekly_consumption  DECIMAL(8,3),
    consumption_confidence  DECIMAL(3,2) DEFAULT 0.0,
    -- 0.0 = bootstrapped estimate, 1.0 = well-learned

    -- Learning signals
    times_ordered           INTEGER DEFAULT 0,
    times_removed_by_user   INTEGER DEFAULT 0,
    times_kept_by_user      INTEGER DEFAULT 0,
    last_user_action        TEXT,
    -- kept | removed | qty_increased | qty_decreased | added_manually
    last_user_action_at     TIMESTAMPTZ,

    -- Status
    is_active               BOOLEAN DEFAULT TRUE,   -- false = user excluded item

    created_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now(),

    UNIQUE(household_id, item_name)
);

CREATE INDEX idx_pantry_household      ON pantry_items(household_id);
CREATE INDEX idx_pantry_reorder        ON pantry_items(household_id, estimated_qty_remaining, reorder_threshold);
CREATE INDEX idx_pantry_category       ON pantry_items(household_id, category);
```

---

### 1.8 loop_runs

```sql
CREATE TABLE loop_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id        UUID NOT NULL REFERENCES households(id),

    -- Trigger
    triggered_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger_type        TEXT NOT NULL DEFAULT 'scheduled',
    -- scheduled | manual | onboarding_preview

    -- Stage tracking
    state               TEXT NOT NULL DEFAULT 'pending',
    -- pending | sensing | planning | optimizing |
    -- awaiting_confirmation | placing | completed | skipped | failed | paused

    -- Timing
    sense_started_at        TIMESTAMPTZ,
    sense_completed_at      TIMESTAMPTZ,
    plan_started_at         TIMESTAMPTZ,
    plan_completed_at       TIMESTAMPTZ,
    optimize_started_at     TIMESTAMPTZ,
    optimize_completed_at   TIMESTAMPTZ,
    confirm_sent_at         TIMESTAMPTZ,
    confirm_responded_at    TIMESTAMPTZ,
    place_started_at        TIMESTAMPTZ,
    place_completed_at      TIMESTAMPTZ,

    -- User response
    user_action         TEXT,
    -- confirmed | edited_then_confirmed | skipped | timed_out
    time_to_respond_sec INTEGER,

    -- Outcome
    order_id            UUID REFERENCES orders(id),
    skip_reason         TEXT,
    failure_reason      TEXT,
    failure_stage       TEXT,

    -- LLM metadata
    llm_model           TEXT,
    llm_tokens_used     INTEGER,
    llm_latency_ms      INTEGER,

    -- Substitutions
    substitutions_count INTEGER DEFAULT 0,
    items_unavailable   INTEGER DEFAULT 0,

    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_loop_runs_household   ON loop_runs(household_id);
CREATE INDEX idx_loop_runs_state       ON loop_runs(state);
CREATE INDEX idx_loop_runs_triggered   ON loop_runs(triggered_at DESC);
```

---

### 1.9 loop_run_items

```sql
CREATE TABLE loop_run_items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    loop_run_id         UUID NOT NULL REFERENCES loop_runs(id) ON DELETE CASCADE,
    household_id        UUID NOT NULL REFERENCES households(id),

    -- Item identity
    item_name           TEXT NOT NULL,          -- generic name
    swiggy_sku_id       TEXT,                   -- resolved SKU from search_products
    swiggy_product_name TEXT,                   -- Swiggy's product display name
    brand               TEXT,

    -- Quantities and pricing
    quantity            DECIMAL(8,3) NOT NULL,
    unit                TEXT NOT NULL,
    unit_price          DECIMAL(10,2),
    total_price         DECIMAL(10,2),

    -- Planning metadata
    added_by            TEXT NOT NULL,
    -- rules_engine | llm | user_added
    add_reason          TEXT,                   -- human-readable reason

    -- Substitution tracking
    is_substitution     BOOLEAN DEFAULT FALSE,
    original_item_name  TEXT,                   -- what was originally planned
    substitution_reason TEXT,

    -- User edit outcome
    user_action         TEXT,
    -- kept | removed | qty_changed
    final_quantity      DECIMAL(8,3),           -- quantity after user edits

    -- Stock
    was_in_stock        BOOLEAN DEFAULT TRUE,

    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_run_items_loop_run    ON loop_run_items(loop_run_id);
CREATE INDEX idx_run_items_household   ON loop_run_items(household_id);
```

---

### 1.10 loop_run_edits

```sql
-- Captures every edit action a user makes during confirmation
-- Used for learning and audit

CREATE TABLE loop_run_edits (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    loop_run_id     UUID NOT NULL REFERENCES loop_runs(id) ON DELETE CASCADE,
    household_id    UUID NOT NULL REFERENCES households(id),

    edit_type       TEXT NOT NULL,
    -- remove_item | add_item | change_qty
    item_name       TEXT NOT NULL,
    original_qty    DECIMAL(8,3),
    new_qty         DECIMAL(8,3),
    edit_reason     TEXT,           -- user-provided reason (optional, V1.5)

    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_edits_loop_run    ON loop_run_edits(loop_run_id);
CREATE INDEX idx_edits_household   ON loop_run_edits(household_id);
CREATE INDEX idx_edits_item        ON loop_run_edits(household_id, item_name);
```

---

### 1.11 orders

```sql
CREATE TABLE orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id        UUID NOT NULL REFERENCES households(id),
    loop_run_id         UUID REFERENCES loop_runs(id),

    -- Swiggy order details
    swiggy_order_id     TEXT UNIQUE NOT NULL,
    swiggy_address_id   TEXT NOT NULL,
    delivery_slot       TEXT,

    -- Financials
    item_total          DECIMAL(10,2) NOT NULL,
    delivery_fee        DECIMAL(10,2) DEFAULT 0,
    taxes               DECIMAL(10,2) DEFAULT 0,
    grand_total         DECIMAL(10,2) NOT NULL,

    -- Status
    status              TEXT NOT NULL DEFAULT 'placed',
    -- placed | out_for_delivery | delivered | cancelled

    placed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at        TIMESTAMPTZ,

    -- Pantry update status
    pantry_updated      BOOLEAN DEFAULT FALSE,
    pantry_updated_at   TIMESTAMPTZ,

    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_orders_household      ON orders(household_id);
CREATE INDEX idx_orders_swiggy_id      ON orders(swiggy_order_id);
CREATE INDEX idx_orders_pantry_update  ON orders(pantry_updated, placed_at)
    WHERE pantry_updated = FALSE;
```

---

### 1.12 order_items

```sql
CREATE TABLE order_items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id            UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    household_id        UUID NOT NULL REFERENCES households(id),

    swiggy_sku_id       TEXT NOT NULL,
    product_name        TEXT NOT NULL,
    brand               TEXT,
    category            TEXT,

    quantity            DECIMAL(8,3) NOT NULL,
    unit                TEXT NOT NULL,
    unit_price          DECIMAL(10,2) NOT NULL,
    total_price         DECIMAL(10,2) NOT NULL,

    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_order_items_order     ON order_items(order_id);
CREATE INDEX idx_order_items_household ON order_items(household_id);
CREATE INDEX idx_order_items_sku       ON order_items(household_id, swiggy_sku_id);
```

---

### 1.13 whatsapp_conversations

```sql
-- Tracks active conversation state per household
-- Stored in Redis for speed (see Redis Key Design)
-- Mirrored to Postgres for audit

CREATE TABLE whatsapp_conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id    UUID NOT NULL REFERENCES households(id),

    loop_run_id     UUID REFERENCES loop_runs(id),
    state           TEXT NOT NULL,
    -- awaiting_confirmation | awaiting_edit_detail | edit_in_progress | completed

    last_message_at TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_wa_conv_household ON whatsapp_conversations(household_id);
```

---

### Entity Relationship Summary

```
households
    ├── household_preferences     (1:1)
    ├── household_members         (1:many)
    ├── swiggy_tokens             (1:1)
    ├── addresses                 (1:many)
    ├── brand_preferences         (1:many)
    ├── pantry_items              (1:many)
    ├── loop_runs                 (1:many)
    │       ├── loop_run_items    (1:many)
    │       └── loop_run_edits    (1:many)
    ├── orders                    (1:many)
    │       └── order_items       (1:many)
    └── whatsapp_conversations    (1:many)
```

---

## 2. API Contracts

Base URL: `https://api.pantrypilot.in/v1`

All endpoints return:
```json
{
  "success": true,
  "data": { },
  "error": null
}
```

On error:
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "TOKEN_EXPIRED",
    "message": "Swiggy session has expired. Please reconnect.",
    "retryable": false
  }
}
```

---

### 2.1 Auth Endpoints

#### `POST /auth/initiate`
Begins the OAuth PKCE flow. Generates code verifier + challenge, returns redirect URL.

**Request:** No body (session cookie identifies returning user if any)

**Response:**
```json
{
  "success": true,
  "data": {
    "redirect_url": "https://mcp.swiggy.com/auth/authorize?response_type=code&client_id=...&redirect_uri=...&scope=mcp:tools&state=abc123&code_challenge=xyz&code_challenge_method=S256"
  }
}
```

---

#### `GET /auth/callback?code=...&state=...`
Handles Swiggy's redirect. Validates state, exchanges code for token, creates session.

**Response:** Redirects to `/onboard` (new user) or `/settings` (returning user)

**Errors:**
| Code | Meaning |
|---|---|
| `STATE_MISMATCH` | CSRF attack or stale session |
| `CODE_EXPIRED` | Auth code older than 120s |
| `TOKEN_EXCHANGE_FAILED` | Swiggy rejected the code |

---

#### `POST /auth/logout`
Revokes Swiggy session and clears PantryPilot session.

**Request:** No body

**Response:**
```json
{ "success": true, "data": { "message": "Logged out successfully" } }
```

---

#### `POST /auth/reauth`
Initiates re-authentication for an existing household (token expired).

**Request:**
```json
{ "household_id": "uuid" }
```

**Response:** Same as `/auth/initiate` — returns redirect URL

---

### 2.2 Onboarding Endpoints

#### `POST /onboard/infer`
Runs inference pass on Swiggy order history. Returns what we know about the household.

**Request:** No body (uses session token to identify household)

**Response:**
```json
{
  "success": true,
  "data": {
    "inferred": {
      "diet_type": "vegetarian",
      "diet_confidence": 0.94,
      "weekly_budget_estimate": 2200,
      "preferred_order_day": "sunday",
      "preferred_order_time": "18:30",
      "top_items": [
        { "name": "Aashirvaad Atta", "ordered_times": 8 },
        { "name": "Toor Dal", "ordered_times": 7 }
      ],
      "brand_preferences": [
        { "item": "atta", "brand": "Aashirvaad", "confidence": 0.87 }
      ],
      "addresses": [
        { "swiggy_address_id": "addr_123", "label": "Home", "area": "Koramangala" }
      ]
    },
    "needs_clarification": ["member_count", "weekly_budget"]
  }
}
```

---

#### `POST /onboard/profile`
Saves the household profile from the questionnaire + user confirmations.

**Request:**
```json
{
  "household_type": "couple",
  "member_count": 2,
  "diet_type": "vegetarian",
  "allergies": [],
  "weekly_budget_min": 1500,
  "weekly_budget_max": 2500,
  "preferred_order_day": "sunday",
  "preferred_order_time": "10:00",
  "preferred_address_id": "addr_123",
  "confirmed_inferences": {
    "diet_type": true,
    "brand_preferences": true
  }
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "household_id": "uuid",
    "onboarding_step": "whatsapp_verification"
  }
}
```

---

#### `POST /onboard/whatsapp/send-otp`
Sends OTP to the user's WhatsApp number for verification.

**Request:**
```json
{ "whatsapp_number": "+919876543210" }
```

**Response:**
```json
{
  "success": true,
  "data": { "otp_sent": true, "expires_in_seconds": 600 }
}
```

---

#### `POST /onboard/whatsapp/verify-otp`
Verifies the OTP and links the WhatsApp number to the household.

**Request:**
```json
{ "whatsapp_number": "+919876543210", "otp": "483921" }
```

**Response:**
```json
{
  "success": true,
  "data": {
    "whatsapp_verified": true,
    "onboarding_step": "first_basket_preview"
  }
}
```

---

#### `GET /onboard/basket-preview`
Generates and returns the first basket preview (dry run — no placement).

**Response:**
```json
{
  "success": true,
  "data": {
    "basket": {
      "items": [
        {
          "item_name": "Aashirvaad Atta",
          "product_name": "Aashirvaad Whole Wheat Atta 5kg",
          "quantity": 1,
          "unit": "bag",
          "unit_price": 280,
          "added_by": "rules_engine",
          "add_reason": "Running low — last ordered 12 days ago"
        }
      ],
      "item_count": 13,
      "estimated_total": 1940,
      "budget_max": 2200,
      "substitutions": [],
      "preview_items": 5,
      "remaining_items": 8
    }
  }
}
```

---

#### `POST /onboard/complete`
Marks onboarding complete and schedules first planning loop run.

**Request:**
```json
{
  "send_basket_now": true
  // false = schedule for next preferred day/time
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "onboarding_complete": true,
    "next_basket_at": "2026-06-29T10:00:00+05:30",
    "basket_sent_to_whatsapp": true
  }
}
```

---

### 2.3 Webhook Endpoints

#### `POST /webhooks/whatsapp`
Receives inbound messages from Interakt. Validated by HMAC-SHA256 signature.

**Headers:**
```
X-Interakt-Signature: sha256=<hmac_signature>
```

**Request body (Interakt format):**
```json
{
  "type": "message",
  "payload": {
    "from": "+919876543210",
    "type": "interactive",
    "interactive": {
      "type": "button_reply",
      "button_reply": {
        "id": "confirm_basket",
        "title": "Looks good, order it"
      }
    },
    "timestamp": 1750000000
  }
}
```

**Response:** Always `200 OK` with `{ "received": true }` — processing is async.

---

### 2.4 Settings Endpoints

#### `GET /settings`
Returns current household settings.

#### `PATCH /settings/profile`
Update household type, diet, allergies, budget.

#### `PATCH /settings/preferences`
Update order day/time, delivery slot, confirmation window.

#### `POST /settings/pause`
Pause the planning loop indefinitely.

**Request:**
```json
{ "reason": "travelling" }
```

#### `POST /settings/resume`
Resume a paused loop.

#### `DELETE /settings/account`
Delete household account, all data, revoke Swiggy session.

---

### 2.5 Internal Endpoints (Service-to-Service)

Not exposed publicly. Used by Celery workers and internal services.

#### `POST /internal/loop/trigger`
Manually trigger a planning loop for a household (admin or test use).

#### `GET /internal/loop/{loop_run_id}/status`
Get current state of a loop run.

#### `POST /internal/pantry/update`
Update pantry state post-order (called by Celery worker after order confirmed).

---

## 3. Service Interfaces

### 3.1 AuthService

```python
class AuthService:
    def initiate_oauth(self, session_id: str) -> str:
        """Generate PKCE params, store in Redis, return redirect URL."""

    def handle_callback(self, code: str, state: str, session_id: str) -> Household:
        """Validate state, exchange code, store encrypted token, return household."""

    def get_valid_token(self, household_id: str) -> str:
        """Return decrypted access token. Raise TokenExpiredError if expired."""

    def revoke_session(self, household_id: str) -> None:
        """Call Swiggy /auth/logout, delete token from DB."""

    def check_expiring_tokens(self) -> List[str]:
        """Return household_ids with tokens expiring in <48hrs. Called by daily job."""
```

---

### 3.2 OnboardingService

```python
class OnboardingService:
    def run_inference(self, household_id: str, access_token: str) -> InferenceResult:
        """Pull order history + addresses via MCP, run inference pass."""

    def save_profile(self, household_id: str, profile: HouseholdProfile) -> None:
        """Save questionnaire answers + confirmed inferences to DB."""

    def send_otp(self, household_id: str, whatsapp_number: str) -> None:
        """Generate OTP, store in Redis with TTL, send via WhatsApp Service."""

    def verify_otp(self, household_id: str, whatsapp_number: str, otp: str) -> bool:
        """Validate OTP from Redis. Mark number verified on success."""

    def generate_preview_basket(self, household_id: str) -> Basket:
        """Dry-run planning loop. Return basket without placing order."""

    def complete_onboarding(self, household_id: str, send_now: bool) -> datetime:
        """Bootstrap pantry state, schedule first loop, return next_run_at."""
```

---

### 3.3 PlanningService

```python
class PlanningService:
    def run_loop(self, household_id: str, loop_run_id: str) -> LoopResult:
        """Orchestrate full Sense → Plan → Optimize → Confirm → Place pipeline."""

    def sense(self, household_id: str) -> HouseholdContext:
        """Fetch household profile, apply pantry decay, return context."""

    def plan(self, context: HouseholdContext) -> RevisedCandidateBasket:
        """Run rules engine then LLM enrichment. Return revised basket."""

    def optimize(self, basket: RevisedCandidateBasket, context: HouseholdContext) -> ResolvedBasket:
        """Resolve SKUs via MCP, handle OOS, reconcile budget."""

    def send_for_confirmation(self, household_id: str, basket: ResolvedBasket, loop_run_id: str) -> None:
        """Send basket card via WhatsApp Service, update loop state."""

    def handle_user_response(self, household_id: str, action: str, edits: List[Edit]) -> None:
        """Route confirm / edit / skip to appropriate handler."""

    def place_order(self, household_id: str, basket: ResolvedBasket) -> str:
        """Clear cart, update cart, checkout. Return swiggy_order_id."""
```

---

### 3.4 PantryService

```python
class PantryService:
    def bootstrap(self, household_id: str, order_history: List[Order]) -> None:
        """Build initial pantry state from Swiggy order history."""

    def get_state(self, household_id: str) -> List[PantryItem]:
        """Return pantry items with consumption decay applied."""

    def apply_decay(self, item: PantryItem) -> PantryItem:
        """Compute estimated_qty_remaining based on days elapsed + avg_weekly_consumption."""

    def update_post_order(self, household_id: str, order_id: str) -> None:
        """Fetch order details via MCP, reset quantities for ordered items."""

    def record_user_edit(self, household_id: str, item_name: str, action: str, qty_delta: float) -> None:
        """Log edit, update consumption model signals."""

    def update_consumption_rate(self, household_id: str, item_name: str) -> None:
        """Recalculate avg_weekly_consumption based on accumulated signals."""
```

---

### 3.5 WhatsAppService

```python
class WhatsAppService:
    def send_otp(self, whatsapp_number: str, otp: str) -> None:
        """Send Template 1 (OTP verification)."""

    def send_basket_card(self, household_id: str, basket: ResolvedBasket, loop_run_id: str) -> None:
        """Send Template 2 (basket preview) with 3 interactive buttons."""

    def send_order_receipt(self, household_id: str, order: Order) -> None:
        """Send Template 3 (order receipt)."""

    def send_reauth_reminder(self, household_id: str, urgency: str, expiry_at: datetime) -> None:
        """Send Template 4 (48hr) or Template 5 (24hr) based on urgency."""

    def send_session_expired(self, household_id: str) -> None:
        """Send Template 6 (session expired / order failed)."""

    def handle_inbound(self, from_number: str, message: InboundMessage) -> None:
        """Parse inbound message, route to appropriate handler."""

    def send_text(self, household_id: str, message: str) -> None:
        """Send free-form text (within 24hr conversation window only)."""
```

---

## 4. LangGraph Agent Definition

The Planning Service is built as a LangGraph StateGraph.

### State Schema

```python
from typing import TypedDict, List, Optional
from enum import Enum

class LoopStage(str, Enum):
    SENSE = "sense"
    PLAN_RULES = "plan_rules"
    PLAN_LLM = "plan_llm"
    OPTIMIZE = "optimize"
    CONFIRM = "confirm"
    PLACE = "place"
    DONE = "done"
    FAILED = "failed"

class PlanningState(TypedDict):
    # Identity
    household_id: str
    loop_run_id: str

    # Stage tracking
    current_stage: LoopStage
    error: Optional[str]

    # Sense outputs
    household_context: Optional[HouseholdContext]

    # Plan outputs
    candidate_basket: Optional[CandidateBasket]        # after rules engine
    revised_basket: Optional[RevisedCandidateBasket]   # after LLM

    # Optimize outputs
    resolved_basket: Optional[ResolvedBasket]          # after SKU resolution
    substitutions: List[Substitution]
    unavailable_items: List[str]

    # Confirm state
    confirmation_sent_at: Optional[str]                # ISO timestamp
    user_action: Optional[str]                         # confirmed | edited | skipped | timed_out
    user_edits: List[Edit]

    # Place outputs
    swiggy_order_id: Optional[str]

    # Metadata
    llm_tokens_used: int
    mcp_calls_made: int
```

### Graph Definition

```python
from langgraph.graph import StateGraph, END

def build_planning_graph() -> StateGraph:
    graph = StateGraph(PlanningState)

    # Add nodes
    graph.add_node("sense",      sense_node)
    graph.add_node("plan_rules", plan_rules_node)
    graph.add_node("plan_llm",   plan_llm_node)
    graph.add_node("optimize",   optimize_node)
    graph.add_node("confirm",    confirm_node)
    graph.add_node("place",      place_node)
    graph.add_node("handle_skip", handle_skip_node)
    graph.add_node("handle_failure", handle_failure_node)

    # Entry point
    graph.set_entry_point("sense")

    # Edges
    graph.add_edge("sense",      "plan_rules")
    graph.add_edge("plan_rules", "plan_llm")
    graph.add_edge("plan_llm",   "optimize")
    graph.add_edge("optimize",   "confirm")

    # Conditional: after confirm, route based on user response
    graph.add_conditional_edges(
        "confirm",
        route_after_confirm,
        {
            "place":        "place",
            "re_optimize":  "optimize",   # user edited → re-optimize
            "skip":         "handle_skip",
            "failed":       "handle_failure"
        }
    )

    graph.add_edge("place",          END)
    graph.add_edge("handle_skip",    END)
    graph.add_edge("handle_failure", END)

    # Interrupt before "confirm" — wait for human input
    graph.add_interrupt_before("place")

    return graph.compile(checkpointer=postgres_checkpointer)
```

---

## 5. Celery Task Definitions

### Task: `trigger_planning_loop`

```python
@celery.task(
    bind=True,
    max_retries=2,
    default_retry_delay=300,    # 5 minutes
    queue="planning"
)
def trigger_planning_loop(self, household_id: str):
    """
    Main planning loop task. Triggered by Celery Beat per household schedule.
    """
    try:
        loop_run_id = create_loop_run(household_id, trigger_type="scheduled")
        planning_service.run_loop(household_id, loop_run_id)
        reschedule_next_run(household_id)
    except TokenExpiredError:
        pause_household(household_id, reason="token_expired")
        whatsapp_service.send_session_expired(household_id)
    except SwiggyMCPError as e:
        self.retry(exc=e)
    except Exception as e:
        mark_loop_failed(loop_run_id, str(e))
        raise
```

---

### Task: `update_pantry_post_order`

```python
@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,    # 2 minutes
    queue="pantry"
)
def update_pantry_post_order(self, household_id: str, swiggy_order_id: str):
    """
    Called 30 minutes after successful checkout.
    Fetches confirmed order details and updates pantry state.
    """
    try:
        order_details = mcp_client.get_order_details(swiggy_order_id)
        pantry_service.update_post_order(household_id, order_details)
        mark_order_pantry_updated(swiggy_order_id)
    except Exception as e:
        self.retry(exc=e)
```

---

### Task: `check_token_expiry`

```python
@celery.task(queue="maintenance")
def check_token_expiry():
    """
    Runs daily at 9 AM IST.
    Sends re-auth reminders for tokens expiring within 48 hours.
    """
    expiring_soon = auth_service.check_expiring_tokens()
    for household_id in expiring_soon:
        token = get_token_record(household_id)
        hours_remaining = (token.token_expiry - now()).total_seconds() / 3600

        if hours_remaining <= 24 and not token.nudge_24hr_sent:
            whatsapp_service.send_reauth_reminder(household_id, "24hr", token.token_expiry)
            mark_nudge_sent(household_id, "24hr")

        elif hours_remaining <= 48 and not token.nudge_48hr_sent:
            whatsapp_service.send_reauth_reminder(household_id, "48hr", token.token_expiry)
            mark_nudge_sent(household_id, "48hr")
```

---

### Task: `handle_confirmation_timeout`

```python
@celery.task(queue="planning")
def handle_confirmation_timeout(household_id: str, loop_run_id: str):
    """
    Scheduled 6 hours after basket card is sent.
    If user hasn't responded, auto-skip this cycle.
    """
    loop_run = get_loop_run(loop_run_id)
    if loop_run.state == "awaiting_confirmation":
        mark_loop_skipped(loop_run_id, reason="timeout")
        reschedule_next_run(household_id)
```

---

### Celery Beat Schedule

```python
CELERYBEAT_SCHEDULE = {
    # Per-household loop triggers — dynamically managed
    # Each household has its own periodic task entry

    "daily-token-expiry-check": {
        "task": "tasks.check_token_expiry",
        "schedule": crontab(hour=9, minute=0),      # 9 AM IST daily
    },

    "hourly-missed-run-catchup": {
        "task": "tasks.catchup_missed_runs",
        "schedule": crontab(minute=5),              # :05 every hour
    },
}
```

---

## 6. MCP Client Wrapper

Wraps all Swiggy Instamart MCP tool calls. Handles auth injection, error normalisation, and logging.

```python
class SwiggyMCPClient:
    BASE_URL = "https://mcp.swiggy.com/im"

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

    def search_products(self, query: str, address_id: str) -> List[Product]:
        """Search catalogue. Returns ranked list of matching SKUs."""

    def get_cart(self) -> Cart:
        """Fetch current cart contents and bill breakdown."""

    def update_cart(self, items: List[CartItem]) -> Cart:
        """Replace cart contents with provided items."""

    def clear_cart(self) -> None:
        """Remove all items from cart."""

    def checkout(self, address_id: str, delivery_slot: str) -> Order:
        """Place order. Returns Swiggy order details."""

    def get_orders(self, limit: int = 20) -> List[Order]:
        """Fetch order history. Used at onboarding and for pantry sync."""

    def get_order_details(self, order_id: str) -> OrderDetail:
        """Fetch full details of a specific order."""

    def get_addresses(self) -> List[Address]:
        """Fetch saved delivery addresses."""

    def track_order(self, order_id: str) -> OrderStatus:
        """Get real-time order status."""

    def _call(self, tool_name: str, params: dict) -> dict:
        """
        Internal MCP tool call with:
        - Auth header injection
        - Retry on 5xx (max 2 retries, 500ms backoff)
        - 401 → raise TokenExpiredError
        - 419 → raise SessionRevokedError
        - Structured logging (tool_name, latency, status — NO token in logs)
        """
```

---

## 7. Redis Key Design

```
# OAuth PKCE flow (TTL: 10 minutes)
auth:pkce:{session_id}          → { code_verifier, state, created_at }

# WhatsApp OTP (TTL: 10 minutes)
otp:{household_id}              → { otp_hash, whatsapp_number, attempts }

# Conversation state (TTL: 8 hours)
conv:{household_id}             → {
                                    loop_run_id,
                                    state,           # awaiting_confirmation | edit_in_progress
                                    basket_snapshot, # resolved basket at confirm time
                                    sent_at
                                  }

# Rate limiting (TTL: 1 minute window)
ratelimit:household:{household_id}:{endpoint}   → request_count

# Celery job queue
celery                          → default task queue
celery:planning                 → planning loop tasks
celery:pantry                   → pantry update tasks
celery:maintenance              → token check, cleanup tasks

# Session cache for web app (TTL: 1 hour)
session:{session_id}            → { household_id, created_at }
```

---

## 8. Error Handling

### Error Code Reference

| Code | HTTP | Meaning | Retryable |
|---|---|---|---|
| `TOKEN_EXPIRED` | 401 | Swiggy access token expired | No — needs re-auth |
| `SESSION_REVOKED` | 401 | Swiggy session revoked (code 419) | No — needs re-auth |
| `MCP_UNAVAILABLE` | 503 | Swiggy MCP returned 5xx | Yes — retry 2x |
| `ITEM_OUT_OF_STOCK` | 200 | SKU unavailable at checkout | No — substitute |
| `CART_PRICE_MISMATCH` | 200 | Price changed between optimize and place | No — re-confirm |
| `CHECKOUT_FAILED` | 400 | Swiggy rejected checkout | No — alert user |
| `OTP_EXPIRED` | 400 | OTP TTL exceeded | No — resend |
| `OTP_INVALID` | 400 | Wrong OTP entered | No — retry (max 3) |
| `HOUSEHOLD_PAUSED` | 403 | Loop paused by user | No |
| `RATE_LIMIT_EXCEEDED` | 429 | Too many requests | Yes — backoff |
| `WHATSAPP_SEND_FAILED` | 500 | Interakt API error | Yes — retry 2x |

---

### Error Handling Strategy by Layer

```
MCP Client         → Normalise to internal error codes, log with context
Planning Service   → Catch per-stage, update loop_run state, alert user if order-blocking
WhatsApp Service   → Retry outbound sends 2x before logging failure
API Gateway        → Return structured error response, never expose internals
Celery Tasks       → Retry transient errors, dead-letter permanent failures
```

---

## 9. Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:5432/pantrypilot
REDIS_URL=redis://host:6379/0

# Swiggy MCP
SWIGGY_MCP_BASE_URL=https://mcp.swiggy.com
SWIGGY_CLIENT_ID=<from_dynamic_registration>
SWIGGY_REDIRECT_URI=https://pantrypilot.in/auth/callback

# Token encryption
TOKEN_ENCRYPTION_KEY=<32-byte-hex-key>         # AES-256

# Anthropic Claude
ANTHROPIC_API_KEY=<key>
ANTHROPIC_MODEL=claude-sonnet-4-5

# Interakt (WhatsApp BSP)
INTERAKT_API_KEY=<key>
INTERAKT_WEBHOOK_SECRET=<secret>               # for HMAC signature validation
PANTRYPILOT_WHATSAPP_NUMBER=+91XXXXXXXXXX

# Internal
INTERNAL_API_SECRET=<key>                      # for service-to-service calls
JWT_SECRET=<key>                               # for web session JWTs
JWT_EXPIRY_HOURS=24

# AWS
AWS_REGION=ap-south-1
AWS_SECRETS_MANAGER_PREFIX=pantrypilot/prod/

# Monitoring
SENTRY_DSN=<dsn>
LOG_LEVEL=INFO
```

All secrets fetched from **AWS Secrets Manager** at startup — never hardcoded, never in `.env` files in production.
