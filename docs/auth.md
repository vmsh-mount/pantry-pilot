# PantryPilot — Authentication
*Last updated: 2026-06-25*

---

## Overview

PantryPilot authenticates users via Swiggy's OAuth 2.1 + PKCE flow. We never collect or store Swiggy credentials. The user logs in on Swiggy's own page — PantryPilot only receives an access token that authorises it to act on the user's behalf.

---

## Swiggy OAuth — Key Facts

| Property | Value |
|---|---|
| Standard | OAuth 2.1 with PKCE |
| Grant type | `authorization_code` only (v1.0) |
| Access token lifetime | 5 days |
| User session lifetime | 30 days (sliding window) |
| Refresh tokens | ❌ Not available in v1.0 (planned for v1.1) |
| Redirect URI | HTTPS required in production; `http://localhost` allowed in dev |
| Scopes needed | `mcp:tools` |
| Base URL | `https://mcp.swiggy.com` |

### Swiggy Auth Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /auth/authorize` | Initiates the consent + login flow |
| `POST /auth/token` | Exchanges auth code for access token |
| `POST /auth/logout` | Revokes session |
| `GET /.well-known/oauth-authorization-server` | OAuth metadata (RFC 8414) |

---

## Full Auth Flow — Step by Step

### Step 1: User lands on pantrypilot.in

- User sees the PantryPilot landing page
- Clicks **"Connect your Swiggy account"**

### Step 2: PKCE Generation (Backend)

Before redirecting the user, our backend generates:
- `code_verifier` — a cryptographically random string (43–128 chars)
- `code_challenge` — SHA-256 hash of the verifier, base64url encoded

```
code_challenge = BASE64URL(SHA256(code_verifier))
code_challenge_method = S256
```

Both are generated server-side and the `code_verifier` is stored temporarily in the user's session (never sent to Swiggy until Step 5).

### Step 3: Redirect to Swiggy

User's browser is redirected to:

```
GET https://mcp.swiggy.com/auth/authorize
  ?response_type=code
  &client_id=<our_registered_client_id>
  &redirect_uri=https://pantrypilot.in/auth/callback
  &scope=mcp:tools
  &state=<random_csrf_token>
  &code_challenge=<generated_above>
  &code_challenge_method=S256
```

- `state` is a random token stored in the user's session — used to prevent CSRF attacks
- User sees Swiggy's own login page (phone number + OTP)
- PantryPilot never sees the phone number or OTP

### Step 4: User Authenticates on Swiggy

- Swiggy collects phone number + OTP internally
- On success, Swiggy redirects back to our `redirect_uri`:

```
GET https://pantrypilot.in/auth/callback
  ?code=<authorization_code>
  &state=<same_state_we_sent>
```

- Authorization code is **single-use** and expires in **120 seconds**

### Step 5: Validate State + Exchange Code (Backend)

Our backend:
1. Validates that `state` matches what we stored in Step 3 (CSRF check)
2. Immediately exchanges the code for an access token:

```
POST https://mcp.swiggy.com/auth/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=<authorization_code>
&redirect_uri=https://pantrypilot.in/auth/callback
&client_id=<our_registered_client_id>
&code_verifier=<from_step_2>
```

### Step 6: Receive + Store Access Token

Swiggy responds with:

```json
{
  "access_token": "...",
  "token_type": "Bearer",
  "expires_in": 432000
}
```

Our backend:
- Stores `access_token` encrypted at rest (AES-256) in Postgres, scoped to the user's household
- Stores `token_expiry` = now + 5 days
- **Never logs the token in plaintext**
- **Never exposes the token to the frontend**

### Step 7: Onboarding Begins

With a valid token, we immediately:
1. Call `get_orders` to pull Swiggy order history
2. Call `get_addresses` to fetch saved delivery addresses
3. Begin the household profile onboarding flow

---

## Token Expiry Handling

This is the most critical operational concern in v1.0, since Swiggy does not issue refresh tokens.

### The Problem

- Access tokens expire after 5 days
- Our weekly planning loop runs autonomously
- If the token expires mid-cycle and we attempt `search_products` or `checkout`, the call returns **401**
- A silent failure here = missed grocery order for the household

### Our Strategy: Proactive Re-auth

We never let the token expire without warning the user first.

#### Timeline

| Time Before Expiry | Action |
|---|---|
| 48 hours | WhatsApp message: "Your Swiggy session expires in 2 days. Tap here to reconnect." |
| 24 hours | Second WhatsApp reminder if not yet re-authenticated |
| At expiry | Pause all autonomous actions for this household. Send final WhatsApp alert. |
| After expiry | No MCP calls attempted until user re-authenticates |

#### Re-auth Flow

Re-authentication follows the exact same OAuth flow (Steps 1–6). The user taps a link in WhatsApp → lands on `pantrypilot.in/reauth` → connects Swiggy → new token stored → loop resumes.

### 401 Handling at Runtime

Even with proactive reminders, a 401 can occur (user ignored reminders, token revoked manually, etc.).

On any 401 from Swiggy MCP:
1. **Immediately pause** all pending MCP calls for this household
2. Send WhatsApp alert: *"We couldn't place your order — your Swiggy session expired. Tap here to reconnect."*
3. Log the failure in the audit trail with timestamp and which tool call triggered it
4. On code **419** (session revoked by Swiggy): treat same as 401, but note in logs that revocation was server-side

---

## Security Requirements

### Token Storage
- Access tokens encrypted at rest: **AES-256**
- Stored server-side only — never in browser localStorage, cookies, or frontend state
- Access scoped per household row in Postgres

### Transport
- All communication over **TLS 1.3**
- No token transmitted over non-HTTPS channels under any circumstance

### PKCE
- `code_verifier` generated with `secrets.token_urlsafe(96)` (Python) or equivalent
- Never reused across sessions
- Discarded immediately after token exchange

### State Parameter
- Random 32-byte token generated per auth session
- Validated on callback before any token exchange
- Discarded after validation

### Logging
- Tokens never appear in application logs
- Auth events logged (user_id, timestamp, success/failure, token expiry set) — no token value
- Break-glass access to token store requires approval + audit trail entry

---

## Registration with Swiggy

Swiggy uses **Dynamic Client Registration** — no pre-issued client ID needed for development.

For production:
- Submit redirect URI (`https://pantrypilot.in/auth/callback`) to builders@swiggy.in
- Await exact-match allowlist confirmation
- Egress IPs (static NAT gateway) to be shared for MCP call whitelisting

---

## Development vs Production

| | Development | Production |
|---|---|---|
| Redirect URI | `http://localhost:8000/auth/callback` | `https://pantrypilot.in/auth/callback` |
| Base URL | `https://mcp.swiggy.com` (same) | `https://mcp.swiggy.com` |
| Client registration | Dynamic, no approval needed | Requires Swiggy allowlist |
| Token storage | In-memory or local DB | Encrypted Postgres |

---

## Open Questions

- [ ] Does Swiggy v1.1 refresh token timeline have an ETA? (Ask builders@swiggy.in)
- [ ] What scopes does `get_orders` require — `mcp:tools` sufficient?
- [ ] Is there a Swiggy sandbox with test accounts so we can run the full flow without real OTPs?
- [ ] What is the exact `client_id` format after Dynamic Client Registration?
- [ ] Does Swiggy support silent re-auth (prompt=none) for returning users?
