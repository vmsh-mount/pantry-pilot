import logging
from app.redis import get_redis

_log = logging.getLogger("providers.otp.redis")

_OTP_TTL = 600
_KEY_PREFIX = "otp"


def _key(household_id: str, phone: str) -> str:
    return f"{_KEY_PREFIX}:{household_id}:{phone}"


class RedisOTPProvider:
    async def send(self, household_id: str, phone: str, otp: str) -> None:
        # Actual WhatsApp sending is handled by WhatsAppProvider; this logs only.
        _log.info(f"OTP generated for {phone}: {otp}")

    async def store(self, household_id: str, phone: str, otp: str, ttl_seconds: int = _OTP_TTL) -> None:
        redis = await get_redis()
        await redis.set(_key(household_id, phone), f"{otp}:0", ex=ttl_seconds)

    async def get(self, household_id: str, phone: str) -> str | None:
        redis = await get_redis()
        return await redis.get(_key(household_id, phone))

    async def delete(self, household_id: str, phone: str) -> None:
        redis = await get_redis()
        await redis.delete(_key(household_id, phone))

    async def increment_attempts(self, household_id: str, phone: str) -> int:
        redis = await get_redis()
        key = _key(household_id, phone)
        val = await redis.get(key)
        if val is None:
            return 0
        parts = val.split(":")
        attempts = int(parts[1]) + 1
        ttl = await redis.ttl(key)
        await redis.set(key, f"{parts[0]}:{attempts}", ex=max(ttl, 1))
        return attempts
