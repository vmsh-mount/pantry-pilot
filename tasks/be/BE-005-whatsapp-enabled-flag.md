# BE-005 — WhatsApp Enabled Flag

**Status:** ✅ Done  
**Area:** Backend + Frontend  
**Depends on:** nothing

---

## Problem

During early development, WhatsApp integration adds friction without providing value. Basket previews and order receipts get sent to a real phone number while the pipeline is still being iterated on. The OTP verification step in onboarding blocks testing the full flow whenever Twilio is unreliable or unconfigured. There is no clean way to silence all WhatsApp I/O without commenting out code.

---

## Design principle

**Gate at the WhatsApp service layer. The flag is read in exactly one place.**

When `PANTRYPILOT_WHATSAPP_ENABLED=false`:
- `WhatsAppService` methods (`send_otp`, `send_basket_preview`, `send_order_receipt`, `send_message`) check the flag once at entry and return immediately without calling the provider
- All calls are logged at info level so it is clear messages were intentionally suppressed
- The rest of the pipeline is completely unaware of the flag — `confirm`, `place`, onboarding handlers call the service as normal

The `confirm` node still sets the run state to `awaiting_confirmation` even when the WA preview is suppressed — the dashboard UI confirm button remains the confirmation path.

The flag does **not** live in the MCP client, planning graph, or any API handler. `WhatsAppService` is the single enforcement point.

---

## Toggle

**`PANTRYPILOT_WHATSAPP_ENABLED=false`** in `.env`

- Read into `settings.whatsapp_enabled: bool` in `app/config.py`
- Default: `False` (WhatsApp off — safe for local dev)
- To enable: set `PANTRYPILOT_WHATSAPP_ENABLED=true` in `.env`, run `make restart`
- To disable: set to `false` or remove, run `make restart`

---

## Backend changes

### `app/config.py`

Add one field — Pydantic-settings auto-maps `PANTRYPILOT_WHATSAPP_ENABLED` → `whatsapp_enabled`:

```python
whatsapp_enabled: bool = False
```

### `app/services/whatsapp_service.py`

Add a guard at the top of every public send method:

```python
if not get_settings().whatsapp_enabled:
    logger.info("whatsapp_disabled_skip", method="send_basket_preview")
    return
```

Methods affected: `send_otp`, `send_basket_preview`, `send_order_receipt`, `send_reauth_48hr`, `send_reauth_24hr`, `send_session_expired`, `send_text`. These are all independent methods — none delegates to a common internal method, so each must be guarded individually.

The flag is read via `get_settings()` at runtime inside each method (not injected via constructor). This is consistent with how `dry_run` is read elsewhere in the codebase. Tests patch `app.services.whatsapp_service.get_settings` — do **not** try to inject settings via the constructor.

This is the **only** place the flag is read in backend code.

### `app/services/household_service.py` — expose in `GET /v1/settings`

Add `whatsapp_enabled: bool` to the settings response so the frontend can adapt:

```python
"whatsapp_enabled": _get_settings().whatsapp_enabled,
```

No write endpoint — this is infrastructure config, not a user preference. Toggle via `.env` only.

---

## Onboarding flow when disabled

When `whatsapp_enabled=false`, the OTP phone-verify step (steps 5–6 in the onboarding wizard) has no utility — no OTP will be sent and the user cannot verify. The frontend skips these steps entirely.

**Frontend reads `whatsapp_enabled` from `GET /v1/settings` on the onboarding page mount** (alongside the existing `/onboard/status` call). When `false`, the step index jumps directly from step 4 (inference) to step 7 (basket preview), bypassing phone entry and OTP input.

`GET /v1/settings` is safe to call during onboarding. The endpoint queries the `households` table directly (row created at OAuth callback) and handles a missing `household_preferences` row gracefully — it returns `{}` for the preferences block. The endpoint will not 404 for a mid-onboarding user.

However: if `GET /v1/settings` fails for any reason (network, unexpected error), the frontend must **default `whatsapp_enabled` to `true`** so steps are not silently skipped due to a fetch error. A failed settings call should never suppress the OTP step.

`whatsapp_verified` on the household record remains `false` — this is safe. Confirmed by grep: the field is only read/written in the onboarding flow (`api/onboard.py`, `services/onboarding_service.py`). The planning graph, Celery tasks, and basket API never gate on it. No blocker.

---

## `confirm` node behaviour when disabled

The `confirm` node in `planning_graph.py` calls `wa.send_basket_preview(...)`. When WA is disabled, this call returns immediately (no-op). The node continues to update the run state to `awaiting_confirmation` and returns — behaviour is identical to the enabled path, minus the outbound message.

The planning graph itself requires **no changes**. The no-op is transparent.

---

## Frontend changes

### `app/cockpit/src/lib/api.ts`

Add `whatsapp_enabled: boolean` to `SettingsResponse` type.

### `app/cockpit/src/app/onboard/page.tsx`

Fetch settings on mount alongside `/onboard/status`. When `whatsapp_enabled=false`, skip steps 5 and 6 (phone number entry and OTP verify).

### `app/cockpit/src/app/dashboard/page.tsx`

When `whatsapp_enabled=false`, show a persistent info banner:

```
ℹ WhatsApp off — confirm orders via this dashboard
```

Colour: blue/slate (informational). Amber is reserved for dry run mode which carries higher urgency (real money risk).

No dismiss button — stays visible as long as the flag is active.

---

## Files to touch

**Backend:**
- `app/pilot/app/config.py` — add `whatsapp_enabled` field
- `app/pilot/app/services/whatsapp_service.py` — no-op guard in all send methods
- `app/pilot/app/services/household_service.py` — expose `whatsapp_enabled` in settings response
- `app/pilot/.env.example` — document the flag with an explicit production warning (see note below)

> **`.env.example` note:** The entry must make the production risk clear:
> ```
> # ── WhatsApp integration ──────────────────────────────────────────────────────
> # Set to true in production — false silently disables all outbound WhatsApp
> # messages (basket previews, receipts, OTPs). Default is false for local dev.
> PANTRYPILOT_WHATSAPP_ENABLED=false
> ```
>
> Local `.env` is gitignored — add `PANTRYPILOT_WHATSAPP_ENABLED=false` there manually after implementation.

**Frontend:**
- `app/cockpit/src/lib/api.ts` — add field to `SettingsResponse`
- `app/cockpit/src/app/onboard/page.tsx` — skip OTP steps when disabled
- `app/cockpit/src/app/dashboard/page.tsx` — info banner

**Tests:**
- `app/pilot/tests/unit/test_whatsapp_service.py` — verify all send methods no-op when disabled
- `app/pilot/tests/integration/test_whatsapp_flag.py` — new file

---

## Acceptance criteria

- [ ] `PANTRYPILOT_WHATSAPP_ENABLED=false` suppresses all outbound WhatsApp messages
- [ ] No code other than `WhatsAppService` reads `settings.whatsapp_enabled`
- [ ] `confirm` node still sets run to `awaiting_confirmation` when WA is disabled
- [ ] Onboarding skips phone entry + OTP steps when `whatsapp_enabled=false`
- [ ] `GET /v1/settings` returns `whatsapp_enabled: false`
- [ ] Dashboard shows blue info banner when `whatsapp_enabled=false`
- [ ] Banner absent when `whatsapp_enabled=true`
- [ ] `PANTRYPILOT_WHATSAPP_ENABLED=true` — messages sent normally, no regression
- [ ] Suppressed sends are logged at info level with method name
- [ ] Unit test: each send method returns without calling provider when flag is false (patch `app.services.whatsapp_service.get_settings`)
- [ ] Integration test: full pipeline reaches `awaiting_confirmation` with WA disabled, no WA provider calls made
- [ ] Integration test: onboarding completes successfully with WA disabled — no OTP step hit, `onboarding_complete=true` in DB

---

## Out of scope

- Per-household WhatsApp toggle (flag is global infrastructure config)
- UI toggle for the flag (env var only, intentionally requires restart)
- Inbound WhatsApp message processing — webhooks still receive and process messages regardless of this flag (outbound only)
