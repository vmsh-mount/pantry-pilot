"""
PantryPilot custom exceptions.
All service-layer errors inherit from PantryPilotError.
"""


class PantryPilotError(Exception):
    """Base exception for all PantryPilot errors."""
    code: str = "INTERNAL_ERROR"
    retryable: bool = False

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)


# ── Auth errors ───────────────────────────────────────────────────────────────

class TokenExpiredError(PantryPilotError):
    """Swiggy access token has expired. User must re-authenticate."""
    code = "TOKEN_EXPIRED"
    retryable = False


class SessionRevokedError(PantryPilotError):
    """Swiggy session was revoked server-side (HTTP 419)."""
    code = "SESSION_REVOKED"
    retryable = False


class StateMismatchError(PantryPilotError):
    """OAuth state parameter doesn't match — possible CSRF attack."""
    code = "STATE_MISMATCH"
    retryable = False


class AuthCodeExpiredError(PantryPilotError):
    """OAuth authorization code has expired (> 120s) or already used."""
    code = "CODE_EXPIRED"
    retryable = False


class TokenExchangeError(PantryPilotError):
    """Swiggy rejected the token exchange request."""
    code = "TOKEN_EXCHANGE_FAILED"
    retryable = False


# ── OTP errors ────────────────────────────────────────────────────────────────

class OTPExpiredError(PantryPilotError):
    """OTP has expired (> 10 minutes)."""
    code = "OTP_EXPIRED"
    retryable = False


class OTPInvalidError(PantryPilotError):
    """OTP is incorrect."""
    code = "OTP_INVALID"
    retryable = False


class OTPMaxAttemptsError(PantryPilotError):
    """Too many incorrect OTP attempts."""
    code = "OTP_MAX_ATTEMPTS"
    retryable = False


# ── MCP / Swiggy errors ───────────────────────────────────────────────────────

class SwiggyMCPError(PantryPilotError):
    """Swiggy MCP returned an unexpected error."""
    code = "MCP_UNAVAILABLE"
    retryable = True


class ItemOutOfStockError(PantryPilotError):
    """Item is out of stock on Instamart."""
    code = "ITEM_OUT_OF_STOCK"
    retryable = False


class CheckoutFailedError(PantryPilotError):
    """Swiggy rejected the checkout request."""
    code = "CHECKOUT_FAILED"
    retryable = False


class CartPriceMismatchError(PantryPilotError):
    """Cart total changed significantly between optimize and place stages."""
    code = "CART_PRICE_MISMATCH"
    retryable = False


# ── Household errors ──────────────────────────────────────────────────────────

class HouseholdNotFoundError(PantryPilotError):
    """Household record not found."""
    code = "HOUSEHOLD_NOT_FOUND"
    retryable = False


class HouseholdPausedError(PantryPilotError):
    """Planning loop is paused for this household."""
    code = "HOUSEHOLD_PAUSED"
    retryable = False


class NotAuthenticatedError(PantryPilotError):
    """Request is missing a valid session."""
    code = "NOT_AUTHENTICATED"
    retryable = False
