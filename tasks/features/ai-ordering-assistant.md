# PRD — AI Ordering Assistant (In-App Chat)

**Status:** Ready for implementation
**Date:** 2026-08-11
**Channel:** New in-app chat screen in the cockpit (not WhatsApp — see [Channel Decision](#channel-decision-why-not-whatsapp))
**Related:** orchestrates existing systems, doesn't reimplement them — [`routines.md`](routines.md), the Quick Order basket flow (`app/api/quick.py`), [`nutrition-gap-to-cart-phase-b3-gap-detection.md`](nutrition-gap-to-cart-phase-b3-gap-detection.md)

---

## Goal

A conversational, text-based assistant in the cockpit that can, on request: schedule and edit Routines, build and check out a Quick Order, and answer questions about the household's nutrition status — in natural language, instead of navigating forms.

## What Already Exists — This Is an Orchestration Layer, Not New Backend Logic

Every capability this PRD needs already has a working, tested service underneath it:

| Capability | Already exists at |
|---|---|
| Create/edit/pause/resume/delete a routine | `app/api/routines.py` — full CRUD, already live |
| Search catalog, add/remove basket items, checkout | `app/api/quick.py` — full basket flow, already live |
| Weekly nutrition totals, gaps, targets | `app/api/nutrition.py` — already live |

This PRD's actual scope is narrow: **understand a natural-language request, map it to one of these existing calls with the right parameters, and — for anything that mutates state — get an explicit confirmation before executing.** No new domain logic for routines, baskets, or nutrition is being built here.

## Channel Decision: Why Not WhatsApp

WhatsApp already has a working intent-routing system (`_classify_intent`, `app/services/whatsapp_service.py:341`) and is the product's established primary interface. It was the more obvious extension point, but the decision here is an **in-app chat screen** instead — chosen specifically because responses can be rich UI (a routine preview card, a basket summary, a nutrition chart) rather than constrained to WhatsApp's plain-text/template limitations (CLAUDE.md: *"Twilio requires Content Templates for all WhatsApp messages — plain text is blocked"*). A future pass could expose the same underlying router on WhatsApp too, once the core assistant is proven — noted in [Out of Scope](#out-of-scope), not built here.

## Non-Negotiable Constraint: Confirm, Don't Decide

This product's own stated design principle (`tasks/demo/product-document.md` / the Swiggy proposal): *"Automation earns the right to act by asking first, every time, until trust is earned... There is no code path in this product where an order is placed without an explicit human yes."* The assistant does not get an exception to this. Concretely:

- **Read-only tool calls** (nutrition queries, listing routines, checking basket contents) execute immediately and respond in the same turn — no confirmation needed, nothing to confirm.
- **Any tool call that mutates state** — `create_routine`, `edit_routine`, `pause_routine`/`resume_routine`, `delete_routine`, `add_item_to_basket`, `remove_item_from_basket`, and especially `checkout_quick_order` — is **proposed, not executed**, on the turn the model decides to call it. The chat UI renders a preview card (what's about to happen, in plain terms) with explicit Confirm/Cancel controls. The tool only actually runs after the user taps Confirm.

This applies even to `create_routine`, which might look read-adjacent at first glance — it isn't. A routine, once created, **fires and places real orders on a schedule with zero further confirmation** (that's the entire point of Routines — CLAUDE.md: *"fires on schedule without going through Flow's sense/plan/optimize cycle at all"*). Creating one is the moment of human authorization for every future purchase it will make; that authorization has to be an explicit, deliberate tap in this UI, the same way filling out and submitting the existing routine-creation form already is — not a side effect of the model deciding a sentence sounded like agreement.

## Architecture Decision: Native Tool-Use, Not Prompt-for-JSON

`nutrition_resolution.py`'s `_estimate_llm` gets structured output today by asking the model to return JSON in its text response and parsing it — workable for a single-shot estimate, not reliable enough for an assistant that's choosing between multiple possible actions with different required parameters, potentially across several turns. Claude's Messages API supports native tool use (a `tools` parameter with JSON-schema definitions, structured `tool_use` content blocks in the response) — the right mechanism for this, not the same pattern reused.

The existing `LLMProvider` protocol (`app/providers/base.py`) is deliberately minimal — `complete(system, user, max_tokens) -> str` — and is shared across Anthropic/Gemini/Groq specifically so the rest of the app stays provider-agnostic. Tool-use APIs differ meaningfully across providers. Rather than distorting that shared protocol to accommodate one feature, **the assistant talks to the Anthropic SDK directly** (`app/providers/llm/anthropic.py` already constructs an `anthropic.AsyncAnthropic` client — extend that file, don't extend the protocol). This is a deliberate, scoped exception, not a precedent to reuse elsewhere without reconsidering — see [Out of Scope](#out-of-scope).

## Scope for V1 — Tool Set

| Tool | Type | Maps to |
|---|---|---|
| `get_weekly_nutrition` | read | `nutrition_gaps`/weekly endpoint logic, called directly as a function, not over HTTP |
| `get_nutrition_gaps` | read | `nutrition_gaps.compute_gaps` |
| `list_routines` | read | `routines.py`'s list |
| `get_basket` | read | `quick.py`'s `get_basket` |
| `create_routine` | **write** | `RoutineCreate` → routines service |
| `edit_routine` | **write** | `RoutinePatch` → routines service |
| `pause_routine` / `resume_routine` / `delete_routine` | **write** | existing routine actions |
| `search_and_add_to_basket` | **write** (basket-only, pre-checkout) | `quick.py` search + add |
| `remove_from_basket` | **write** (basket-only, pre-checkout) | `quick.py` remove |
| `checkout_basket` | **write, highest-stakes** | no service function exists today — extraction is a prerequisite, see [Design §0](#0-prerequisite-extract-checkout_baskets-service-function--it-doesnt-exist-yet) |

Every "write" row gets the propose-then-confirm treatment from the constraint above, uniformly — not a per-tool judgment call about which ones feel risky enough to warrant it.

## Design

### 0. Prerequisite: extract `checkout_basket`'s service function — it doesn't exist yet

§3's "clean service-layer call" premise holds for every tool above except one. `create_routine`/etc. delegate to `RoutinesService`; `get_basket`/`search_and_add_to_basket`/`remove_from_basket` delegate to `app/services/quick_basket.py`'s `get_basket`/`add_item`/`remove_item`. **`checkout_basket` has no equivalent.** The entire checkout sequence — acquiring the `routine_cart_lock:{household_id}` Redis lock (the exact mechanism that prevents Routines and Quick Order racing on the same basket), building the cart payload, the dry-run branch, the MCP `clear_cart`/`update_cart`/`checkout` calls, and error handling — is written directly in the route handler body (`api/quick.py:340-410`), not in `quick_basket.py` or anywhere else callable.

Implementing `checkout_basket` per §3 as written forces a choice the PRD needs to make explicit rather than leave for whoever builds it to discover mid-implementation:

- **Duplicate the logic** inside the assistant's tool handler — directly contradicts this PRD's own "orchestration layer, not new backend logic" premise, and creates two independent places that must stay in sync on lock semantics, dry-run behavior, and error codes. Rejected.
- **Extract first.** Move `api/quick.py`'s inline checkout body into a real function — `quick_basket.checkout(household_id, db, access_token, swiggy_address_id) -> CheckoutResult` (or a sibling module if `quick_basket.py` isn't the right home for MCP-calling code; either way, a real service function, not a route body) — and have the existing REST route delegate to it. The assistant's `checkout_basket` handler then calls that same function, matching the shape every other tool in this table already has.

**This extraction is a prerequisite for this PRD, not an implementation detail folded into "build `checkout_basket`."** It should land as its own step before the assistant's checkout tool is wired up — partly because it's real, separable work (moving lock/dry-run/error-handling logic without changing its behavior, verified against the existing quick-order checkout tests before anything new is built on top of it), and partly because it's the natural point to also resolve the confirm-time staleness question below, rather than deciding it under the pressure of also building the assistant feature at the same time.

**Related, smaller decision this extraction should settle:** does confirming a proposed `checkout_basket` call re-execute the exact arguments captured at propose-time, or re-validate against current basket/price/stock state at confirm-time? A proposal built when the model first calls the tool can go stale by the time the user taps Confirm — a concurrent Routine run, a price change, an item going out of stock. **Decision: re-validate at confirm-time, don't replay a stale snapshot.** `confirm_tool_call_id` should re-fetch the current basket via `quick_basket.get_basket` and re-run the same checks the extracted `checkout` function already does (empty basket, missing SKUs) at confirm time, not re-execute whatever the basket looked like when it was first proposed — the same lock already prevents a race *during* checkout; this closes the smaller gap between propose and confirm. If the basket materially changed since the proposal (items added/removed by something else in the interim), the confirm response should say so rather than silently checking out a basket that's no longer what was previewed.

### 1. New table: conversation history

```python
class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    id:            Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:  Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"))
    role:          Mapped[str] = mapped_column(String)   # "user" | "assistant" | "tool_result"
    content:       Mapped[str] = mapped_column(Text)
    tool_calls:    Mapped[list | None] = mapped_column(JSONB)   # assistant turns that invoked a tool
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_assistant_messages_household", "household_id", "created_at"),)
```

Persisted (not session-only) — consistent with this codebase's existing bias toward auditable history (`LoopRunEdit`, `ItemSignal` already exist for exactly this reason on the Flow side). Also gives the model real conversation context across page reloads, and is the natural place to bound context window growth later (truncate/summarize older turns) without redesigning storage.

### 2. New endpoint: `POST /v1/assistant/message`

Request: `{"message": str, "confirm_tool_call_id": str | None}`. Two shapes of call:
- A fresh user message → runs the model against the last N turns of history + the tool definitions, returns either a plain text reply or a **proposed** write-tool call (rendered as a preview card, not yet executed).
- A confirmation (`confirm_tool_call_id` set, referencing a previously-proposed call) → executes that specific tool call now, appends the result, and lets the model produce a natural-language follow-up ("Done — your Tuesday milk routine is live.").

Read-only tool calls never produce a `confirm_tool_call_id` round-trip — the backend executes them inline within the same request and returns the model's response directly.

### 3. Tool dispatch

Each tool name maps to a plain Python function that calls the *same service-layer functions the existing REST routes already call* — not a second HTTP request to our own API. E.g. `create_routine`'s handler imports and calls whatever `routines.py`'s `create_routine` route itself calls internally, with the LLM-extracted arguments validated through the existing `RoutineCreate` Pydantic model (free correctness check — malformed LLM output fails validation before it ever reaches the database, same as a malformed API request would).

This premise holds cleanly for every tool except `checkout_basket` — see [Design §0](#0-prerequisite-extract-checkout_baskets-service-function--it-doesnt-exist-yet), which this tool dispatch design depends on as a prerequisite.

### 4. Frontend — new `/assistant` chat screen

Standard chat UI: message list, text input. Two render modes for an assistant turn:
- Plain text response → rendered as a chat bubble.
- Proposed write-tool call → rendered as a distinct **preview card** (not a chat bubble) showing the concrete effect in plain language (e.g. "Create routine: Weekly Milk & Curd — Tuesdays 9am — Amul Toned Milk, Mother Dairy Curd") with Confirm/Cancel buttons. Cancel just ends that proposal (no backend call); Confirm calls the endpoint with `confirm_tool_call_id` set.

## Out of Scope

- **WhatsApp exposure of the same assistant.** Deliberately scoped to in-app chat only for v1 (see [Channel Decision](#channel-decision-why-not-whatsapp)) — the tool-dispatch layer is channel-agnostic by design, so this is additive later, not a rework.
- **Meal logging and nutrition-label-photo upload.** Separate PRDs (per this session's task list) — not integrated into the assistant's tool set in this pass, though `get_weekly_nutrition` would naturally read meal-logged data later once that exists.
- **Voice input.** Text only.
- **Proactive/assistant-initiated messages.** Reactive only — the assistant responds to what the user sends, it doesn't message first. (Flow/Routines already own proactive outreach via WhatsApp; duplicating that here would be a second, conflicting "something reaches out to the user" system.)
- **Extending `LLMProvider` for tool-use generally.** The direct-Anthropic-SDK approach here is scoped to this feature. If a second feature needs tool-use later, that's the point to reconsider whether the shared protocol should grow a real abstraction for it — not before, and not implicitly via this PRD.
- **Multi-household / shared-conversation concerns.** One household, one conversation history, same session-based auth every other cockpit page already uses.

## Testing Plan

**Prerequisite extraction (Design §0), before any assistant code is built on top of it:**
- The existing `POST /v1/basket/checkout` integration tests must pass unchanged against the extracted `quick_basket.checkout(...)` function — this is a refactor, not a behavior change, and needs to be verifiably so before the assistant's `checkout_basket` tool calls it.
- New test: confirming a `checkout_basket` proposal after the basket changed in the interim (item added/removed between propose and confirm) re-validates against current state per the confirm-time decision in §0, rather than checking out whatever was true at propose-time.

**Unit tests** (tool dispatch, isolated from the LLM):
- Each write-tool handler, called directly with valid arguments, produces the same DB state as calling the underlying route handler directly with an equivalent request body.
- Malformed/incomplete LLM-extracted arguments (e.g. missing `frequency_value`) fail Pydantic validation before touching the database.

**Integration tests:**
- A full `POST /v1/assistant/message` round trip for a read-only tool (e.g. "what's my protein status this week") returns a response in one request, no confirmation step.
- A write-tool proposal is *not* executed until the confirming request arrives — assert no DB row exists after the propose-only response, and does exist after confirmation.
- Cancel path: a proposed call that's never confirmed leaves no trace in the routine/basket tables.

**Manual/LLM-quality verification** (not deterministic, tracked separately from pass/fail tests): a fixed set of representative prompts per tool (including deliberately incomplete ones, e.g. "set up a milk routine" with no frequency/time specified) — checked for reasonable clarifying questions rather than the model guessing silently.

## Rollout Notes

- New table, no migration risk to existing data — purely additive.
- Cost/latency: tool-use calls to Claude are billed the same as any other Anthropic API call; no new provider dependency, but this is now a second place (besides `plan_llm`) drawing on `ANTHROPIC_API_KEY` credits — worth keeping in mind if the same "plan_llm fails gracefully if credits are exhausted" concern (CLAUDE.md Known Pitfall #4) applies here too. This assistant should fail with a clear "assistant unavailable" message, not a silent hang, under the same condition.
