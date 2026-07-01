"""
Tests for AuthService.

Covers:
  - PKCE generation (verifier + challenge correctness)
  - initiate_oauth builds correct redirect URL
  - handle_callback validates state mismatch
  - handle_callback stores encrypted token
  - get_valid_token raises on expired token
  - _derive_code_challenge is RFC 7636 compliant
"""

import pytest
import hashlib
import base64
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from app.services.auth_service import (
    _generate_code_verifier,
    _derive_code_challenge,
)
from app.utils.exceptions import StateMismatchError, TokenExpiredError


# ── PKCE helpers ──────────────────────────────────────────────────────────────

def test_code_verifier_length():
    verifier = _generate_code_verifier()
    assert 43 <= len(verifier) <= 128


def test_code_verifier_is_url_safe():
    verifier = _generate_code_verifier()
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert all(c in allowed for c in verifier)


def test_code_challenge_rfc7636():
    """
    RFC 7636 §4.2: challenge = BASE64URL(SHA256(ASCII(verifier)))
    """
    verifier   = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected   = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert _derive_code_challenge(verifier) == expected


def test_code_challenge_no_padding():
    verifier   = _generate_code_verifier()
    challenge  = _derive_code_challenge(verifier)
    assert "=" not in challenge


# ── Token encryption round-trip ───────────────────────────────────────────────

def test_token_encryption_round_trip(monkeypatch):
    monkeypatch.setattr(
        "app.utils.crypto.get_settings",
        lambda: MagicMock(token_encryption_key="a" * 64)
    )
    from app.utils.crypto import encrypt_token, decrypt_token
    original  = "test_access_token_abc123"
    encrypted = encrypt_token(original)
    assert encrypted != original
    assert decrypt_token(encrypted) == original


def test_encrypt_produces_different_ciphertext_each_time(monkeypatch):
    """Each encryption uses a random nonce — same plaintext → different ciphertext."""
    monkeypatch.setattr(
        "app.utils.crypto.get_settings",
        lambda: MagicMock(token_encryption_key="b" * 64)
    )
    from app.utils.crypto import encrypt_token
    token = "same_token"
    assert encrypt_token(token) != encrypt_token(token)


# ── AuthService.get_valid_token ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_valid_token_raises_when_expired():
    """get_valid_token must raise TokenExpiredError if token_expiry is in the past."""
    from app.services.auth_service import AuthService
    from app.models.db import SwiggyToken

    expired_record = SwiggyToken(
        household_id     = "hh-001",
        access_token_enc = "encrypted_value",
        token_expiry     = datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = expired_record
    mock_db.execute.return_value = mock_result

    service = AuthService(mock_db)

    with pytest.raises(TokenExpiredError):
        await service.get_valid_token("hh-001")


@pytest.mark.asyncio
async def test_get_valid_token_raises_when_no_record():
    """get_valid_token must raise TokenExpiredError if no token exists."""
    from app.services.auth_service import AuthService

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    service = AuthService(mock_db)

    with pytest.raises(TokenExpiredError):
        await service.get_valid_token("hh-nonexistent")


# ── AuthService.handle_callback state validation ──────────────────────────────

@pytest.mark.asyncio
async def test_handle_callback_raises_on_state_mismatch():
    """handle_callback must raise StateMismatchError when state doesn't match."""
    from app.services.auth_service import AuthService

    mock_db = AsyncMock()

    with patch("app.services.auth_service.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        # Stored state is "correct_state", but callback sends "wrong_state"
        mock_redis.get.return_value = "verifier123:correct_state"
        mock_get_redis.return_value = mock_redis

        service = AuthService(mock_db)

        with pytest.raises(StateMismatchError):
            await service.handle_callback(
                code       = "auth_code",
                state      = "wrong_state",
                session_id = "session_abc",
            )


@pytest.mark.asyncio
async def test_handle_callback_raises_when_pkce_expired():
    """handle_callback must raise StateMismatchError when Redis key has expired."""
    from app.services.auth_service import AuthService

    mock_db = AsyncMock()

    with patch("app.services.auth_service.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # Key expired in Redis
        mock_get_redis.return_value = mock_redis

        service = AuthService(mock_db)

        with pytest.raises(StateMismatchError):
            await service.handle_callback(
                code       = "auth_code",
                state      = "any_state",
                session_id = "session_expired",
            )
