# app/core/cache.py — same transparent Redis cache strategy previously used in the legacy monolith
import json
import logging
from typing import Any, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class _CacheManager:
    def __init__(self):
        self._client = None
        self._available: Optional[bool] = None

    def _get_client(self):
        if self._available is False:
            return None
        if self._client is not None:
            return self._client
        if not settings.REDIS_URL:
            self._available = False
            return None
        try:
            import redis
            c = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_timeout=0.5,
                socket_connect_timeout=0.5,
            )
            c.ping()
            self._client = c
            self._available = True
            logger.info("Cache layer connected (Redis)")
            return c
        except Exception as exc:
            self._available = False
            logger.warning("Cache unavailable — passthrough mode (%s)", exc)
            return None

    def get(self, key: str) -> Optional[Any]:
        c = self._get_client()
        if c is None:
            return None
        try:
            raw = c.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        c = self._get_client()
        if c is None:
            return
        try:
            c.setex(key, ttl, json.dumps(value, default=str))
        except Exception:
            pass

    def set_if_absent(self, key: str, value: Any, ttl: int = 300) -> bool:
        c = self._get_client()
        if c is None:
            return False
        try:
            return bool(c.set(key, json.dumps(value, default=str), ex=ttl, nx=True))
        except Exception:
            return False

    def delete(self, key: str) -> None:
        c = self._get_client()
        if c is None:
            return
        try:
            c.delete(key)
        except Exception:
            pass

    def delete_pattern(self, pattern: str) -> None:
        c = self._get_client()
        if c is None:
            return
        try:
            keys = c.keys(pattern)
            if keys:
                c.delete(*keys)
        except Exception:
            pass

    def is_healthy(self) -> bool:
        c = self._get_client()
        if c is None:
            return False
        try:
            return bool(c.ping())
        except Exception:
            return False


cache = _CacheManager()
