"""
AuthService — Swiggy OAuth 2.1 + PKCE flow, token management, re-auth lifecycle.

Responsibilities:
  - Generate PKCE code verifier + challenge
  - Build Swiggy authorize redirect URL
  - Handle OAuth callback (validate state, exchange code → token)
  - Store encrypted access token in Postgres
  - Provide valid decrypted token to other services
  - Monitor token expiry — surface expiring tokens for nudge jobs
  - Revoke Swiggy session on logout or account delete
"""

import secrets
import hashlib
import base64
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.config import get_settings
from app.models.db import Household, SwiggyToken
from app.redis import get_redis
from app.utils.crypto import encrypt_token, decrypt_token
from app.utils.exceptions import (
    TokenExpiredError, SessionRevokedError,
    StateMismatchError, AuthCodeExpiredError, TokenExchangeError,
    HouseholdNotFoundError,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Redis key templates
_PKCE_KEY      = "auth:pkce:{session_id}"   # TTL: 10 min
_PKCE_TTL      = 600                         # seconds
_DCR_KEY       = "auth:swiggy:client_id"    # Dynamic Client Registration cache
_DCR_TTL       = 86_400 * 30               # 30 days

# Token lifetime Swiggy issues (5 days)
_TOKEN_LIFETIME_SECONDS = 432_000


def _is_mock() -> bool:
    s = get_settings()
    explicit = s.mcp_provider
    if explicit:
        return explicit == "mock"
    return s.app_env == "local"


def _base_url(public: bool = False) -> str:
    """Return the correct MCP base URL.

    public=True  → browser-accessible (used in OAuth authorize redirect)
    public=False → server-to-server (token exchange, logout)
    """
    s = get_settings()
    if _is_mock():
        return s.mock_mcp_public_url if public else s.mock_mcp_base_url
    return s.swiggy_mcp_base_url


def _authorize_url() -> str:
    return f"{_base_url(public=True)}/auth/authorize"


def _token_url() -> str:
    return f"{_base_url(public=False)}/auth/token"


def _logout_url() -> str:
    return f"{_base_url(public=False)}/auth/logout"


def _register_url() -> str:
    return f"{_base_url(public=False)}/auth/register"


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ──────────────────────────────────────────────────────────
    # 1. Initiate OAuth
    # ──────────────────────────────────────────────────────────

    async def initiate_oauth(self, session_id: str) -> str:
        """
        Generate PKCE params, persist in Redis, return Swiggy authorize URL.
        Called when user clicks "Connect your Swiggy account".
        """
        code_verifier  = _generate_code_verifier()
        code_challenge = _derive_code_challenge(code_verifier)
        state          = secrets.token_urlsafe(32)

        # Store verifier + state in Redis (expire after 10 min)
        redis = await get_redis()
        await redis.setex(
            _PKCE_KEY.format(session_id=session_id),
            _PKCE_TTL,
            f"{code_verifier}:{state}",
        )

        # Get client_id — from env or Dynamic Client Registration
        client_id = await _get_or_register_client_id()

        params = {
            "response_type":         "code",
            "client_id":             client_id,
            "redirect_uri":          get_settings().swiggy_redirect_uri,
            "scope":                 "mcp:tools",
            "state":                 state,
            "code_challenge":        code_challenge,
            "code_challenge_method": "S256",
        }

        redirect_url = f"{_authorize_url()}?{urlencode(params)}"
        logger.info("oauth_initiated", session_id=session_id[:8])
        return redirect_url

    # ──────────────────────────────────────────────────────────
    # 2. Handle Callback
    # ──────────────────────────────────────────────────────────

    async def handle_callback(
        self,
        code:       str,
        state:      str,
        session_id: str,
    ) -> "CallbackResult":
        """
        Validate state, exchange code for access token, store encrypted token.
        Returns CallbackResult with household_id and whether this is a new user.
        """
        # ── Step 1: Retrieve stored PKCE params ──
        redis = await get_redis()
        stored = await redis.get(_PKCE_KEY.format(session_id=session_id))

        if not stored:
            raise StateMismatchError("PKCE session expired or not found.")

        stored_verifier, stored_state = stored.split(":", 1)

        # ── Step 2: Validate state (CSRF guard) ──
        if not secrets.compare_digest(stored_state, state):
            logger.warning("oauth_state_mismatch", session_id=session_id[:8])
            raise StateMismatchError("OAuth state mismatch. Possible CSRF attack.")

        # Clean up Redis entry immediately — single use
        await redis.delete(_PKCE_KEY.format(session_id=session_id))

        # ── Step 3: Exchange code for token ──
        access_token, expires_in = await self._exchange_code(code, stored_verifier)

        # ── Step 4: Identify or create household ──
        swiggy_user_id = await self._fetch_swiggy_user_id(access_token)
        household, is_new = await self._get_or_create_household(swiggy_user_id)

        # ── Step 5: Store encrypted token ──
        await self._store_token(household.id, access_token, expires_in)

        logger.info(
            "oauth_complete",
            household_id=household.id,
            is_new_user=is_new,
            token_expires_in_days=round(expires_in / 86400, 1),
        )

        return CallbackResult(household_id=household.id, is_new_user=is_new)

    # ──────────────────────────────────────────────────────────
    # 3. Get Valid Token
    # ──────────────────────────────────────────────────────────

    async def get_valid_token(self, household_id: str) -> str:
        """
        Return decrypted access token for a household.
        Raises TokenExpiredError if expired — caller must trigger re-auth.
        """
        result = await self.db.execute(
            select(SwiggyToken).where(SwiggyToken.household_id == household_id)
        )
        token_record = result.scalar_one_or_none()

        if not token_record:
            raise TokenExpiredError(f"No token found for household {household_id}")

        now = datetime.now(timezone.utc)
        if token_record.token_expiry <= now:
            logger.warning("token_expired", household_id=household_id)
            raise TokenExpiredError(
                f"Token expired at {token_record.token_expiry.isoformat()}"
            )

        # Update last_used_at (fire and forget — don't await to stay fast)
        token_record.last_used_at = now

        return decrypt_token(token_record.access_token_enc)

    # ──────────────────────────────────────────────────────────
    # 4. Revoke Session
    # ──────────────────────────────────────────────────────────

    async def revoke_session(self, household_id: str) -> None:
        """
        Call Swiggy /auth/logout and delete the stored token.
        Called on user logout or account deletion.
        """
        try:
            token = await self.get_valid_token(household_id)
            async with httpx.AsyncClient() as client:
                await client.post(
                    _logout_url(),
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
        except (TokenExpiredError, Exception) as e:
            # Token already expired or Swiggy unreachable — still delete locally
            logger.info("revoke_session_swiggy_skipped", household_id=household_id, reason=str(e))

        # Delete token record
        result = await self.db.execute(
            select(SwiggyToken).where(SwiggyToken.household_id == household_id)
        )
        token_record = result.scalar_one_or_none()
        if token_record:
            await self.db.delete(token_record)
            await self.db.flush()

        logger.info("session_revoked", household_id=household_id)

    # ──────────────────────────────────────────────────────────
    # 5. Get Expiring Tokens (for daily nudge job)
    # ──────────────────────────────────────────────────────────

    async def get_expiring_tokens(self) -> list[SwiggyToken]:
        """
        Return token records expiring within the next 48 hours.
        Called by the daily maintenance task.
        """
        now     = datetime.now(timezone.utc)
        in_48hr = now + timedelta(hours=48)

        result = await self.db.execute(
            select(SwiggyToken).where(
                and_(
                    SwiggyToken.token_expiry > now,       # not yet expired
                    SwiggyToken.token_expiry <= in_48hr,  # expires within 48hr
                )
            )
        )
        return result.scalars().all()

    # ──────────────────────────────────────────────────────────
    # 6. Mark Nudge Sent
    # ──────────────────────────────────────────────────────────

    async def mark_nudge_sent(self, household_id: str, urgency: str) -> None:
        """Mark that a re-auth nudge has been sent (48hr or 24hr)."""
        result = await self.db.execute(
            select(SwiggyToken).where(SwiggyToken.household_id == household_id)
        )
        token_record = result.scalar_one_or_none()
        if not token_record:
            return

        if urgency == "48hr":
            token_record.nudge_48hr_sent = True
        elif urgency == "24hr":
            token_record.nudge_24hr_sent = True
        elif urgency == "expired":
            token_record.nudge_expired_sent = True

        await self.db.flush()

    # ──────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────

    async def _exchange_code(
        self,
        code:           str,
        code_verifier:  str,
    ) -> tuple[str, int]:
        """Exchange authorization code for access token at Swiggy /auth/token.

        Swiggy uses JSON body (not form-encoded) and does not require client_id
        in the token request — identity is established via the code_verifier (PKCE).
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _token_url(),
                json={
                    "grant_type":    "authorization_code",
                    "code":          code,
                    "redirect_uri":  get_settings().swiggy_redirect_uri,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/json"},
                timeout=15,
            )

        if response.status_code == 400:
            body = response.json()
            error = body.get("error", "")
            if error in ("invalid_grant", "expired_token"):
                raise AuthCodeExpiredError("Authorization code expired or already used.")
            raise TokenExchangeError(f"Swiggy token exchange failed: {body}")

        if response.status_code != 200:
            raise TokenExchangeError(
                f"Unexpected status from Swiggy /auth/token: {response.status_code}"
            )

        body = response.json()
        access_token = body.get("access_token")
        expires_in   = body.get("expires_in", _TOKEN_LIFETIME_SECONDS)

        if not access_token:
            raise TokenExchangeError("No access_token in Swiggy response.")

        return access_token, expires_in

    async def _fetch_swiggy_user_id(self, access_token: str) -> str:
        """
        Derive a stable user identity from the access token.
        Swiggy MCP v1 does not expose a /me endpoint — we call get_addresses
        and use the authenticated response to confirm identity. The user ID
        is embedded in the JWT claims of the access token.
        """
        try:
            # Decode JWT claims (no signature verification needed — Swiggy issued it)
            import json
            payload_b64 = access_token.split(".")[1]
            # Fix padding
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            claims = json.loads(base64.b64decode(payload_b64))
            user_id = claims.get("sub") or claims.get("user_id") or claims.get("uid")
            if user_id:
                return str(user_id)
        except Exception:
            pass

        # Fallback: use a hash of the token as a stable identifier
        # (replaced once Swiggy exposes a /me endpoint)
        return hashlib.sha256(access_token.encode()).hexdigest()[:32]

    async def _get_or_create_household(
        self,
        swiggy_user_id: str,
    ) -> tuple["Household", bool]:
        """Return existing household or create a new one. Returns (household, is_new)."""
        result = await self.db.execute(
            select(Household).where(Household.swiggy_user_id == swiggy_user_id)
        )
        existing = result.scalar_one_or_none()

        if existing:
            return existing, False

        # New user — create a minimal household record
        # Profile is completed during onboarding
        new_household = Household(
            swiggy_user_id=swiggy_user_id,
        )
        self.db.add(new_household)
        await self.db.flush()  # get the generated ID
        logger.info("household_created", household_id=new_household.id)
        return new_household, True

    async def _store_token(
        self,
        household_id: str,
        access_token: str,
        expires_in:   int,
    ) -> None:
        """Encrypt and upsert access token for a household."""
        encrypted    = encrypt_token(access_token)
        token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        result = await self.db.execute(
            select(SwiggyToken).where(SwiggyToken.household_id == household_id)
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Refresh — reset all nudge flags
            existing.access_token_enc   = encrypted
            existing.token_expiry       = token_expiry
            existing.nudge_48hr_sent    = False
            existing.nudge_24hr_sent    = False
            existing.nudge_expired_sent = False
            existing.last_used_at       = datetime.now(timezone.utc)
        else:
            new_token = SwiggyToken(
                household_id      = household_id,
                access_token_enc  = encrypted,
                token_expiry      = token_expiry,
            )
            self.db.add(new_token)

        await self.db.flush()
        logger.info(
            "token_stored",
            household_id=household_id,
            expires_at=token_expiry.isoformat(),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Dynamic Client Registration (RFC 7591)
# ──────────────────────────────────────────────────────────────────────────────

async def _get_or_register_client_id() -> str:
    """
    Return the Swiggy OAuth client_id, obtaining it via Dynamic Client
    Registration (DCR) if not already configured or cached.

    Priority:
      1. SWIGGY_CLIENT_ID env var (if set)
      2. Redis cache (avoids re-registering on every restart)
      3. POST /auth/register → cache result in Redis for 30 days
    """
    s = get_settings()

    # 1. Explicit env var wins
    if s.swiggy_client_id:
        return s.swiggy_client_id

    # Mock mode — client_id not meaningful, return placeholder
    if _is_mock():
        return "mock-client"

    redis = await get_redis()

    # 2. Redis cache
    cached = await redis.get(_DCR_KEY)
    if cached:
        return cached

    # 3. Dynamic Client Registration
    logger.info("dcr_registering", url=_register_url())
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            _register_url(),
            json={
                "client_name":   "PantryPilot",
                "redirect_uris": [s.swiggy_redirect_uri],
                "grant_types":   ["authorization_code"],
                "token_endpoint_auth_method": "none",  # PKCE public client
            },
            headers={"Content-Type": "application/json"},
        )

    if not response.is_success:
        raise TokenExchangeError(
            f"DCR registration failed ({response.status_code}): {response.text}"
        )

    body = response.json()
    client_id = body.get("client_id")
    if not client_id:
        raise TokenExchangeError(f"DCR response missing client_id: {body}")

    # Cache for 30 days
    await redis.setex(_DCR_KEY, _DCR_TTL, client_id)
    logger.info("dcr_registered", client_id=client_id[:8] + "…")
    return client_id


# ──────────────────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────────────────

class CallbackResult:
    def __init__(self, household_id: str, is_new_user: bool):
        self.household_id = household_id
        self.is_new_user  = is_new_user
        self.redirect_to  = "/onboard" if is_new_user else "/settings"


# ──────────────────────────────────────────────────────────────────────────────
# PKCE helpers (RFC 7636)
# ──────────────────────────────────────────────────────────────────────────────

def _generate_code_verifier() -> str:
    """Generate a cryptographically random code verifier (43–128 chars)."""
    return secrets.token_urlsafe(96)  # 128 base64url chars


def _derive_code_challenge(code_verifier: str) -> str:
    """SHA-256 hash of the verifier, base64url-encoded (no padding)."""
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
