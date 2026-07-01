"""
AES-256-GCM encryption for Swiggy access tokens.
Tokens are encrypted before storage and decrypted on read.
The key is never logged or exposed.
"""
import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.config import get_settings

settings = get_settings()


def _get_key() -> bytes:
    key_hex = settings.token_encryption_key
    key = bytes.fromhex(key_hex)
    if len(key) != 32:
        raise ValueError("TOKEN_ENCRYPTION_KEY must be a 32-byte hex string (64 hex chars)")
    return key


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string. Returns base64-encoded nonce+ciphertext."""
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce for GCM
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a base64-encoded nonce+ciphertext back to plaintext."""
    key = _get_key()
    aesgcm = AESGCM(key)
    combined = base64.b64decode(encrypted.encode())
    nonce = combined[:12]
    ciphertext = combined[12:]
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode()
