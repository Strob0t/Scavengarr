"""Redis-Adapter - Async Redis via redis.asyncio."""

from __future__ import annotations

import asyncio
import pickle
from typing import Any, Optional

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

log = structlog.get_logger(__name__)


class RedisAdapter:
    """Async Redis Cache mit Connection-Pooling via Semaphore.

    - Nutzt `redis.asyncio.Redis` (async-native, kein to_thread nötig).
    - Semaphore limitiert parallele Redis-Ops (verhindert Connection-Exhaustion).
    - Serialisierung via pickle (konsistent mit Diskcache-Adapter).

    Args:
        url: Redis-URL (z. B. `redis://localhost:6379/0`).
        ttl_seconds: Standard-TTL.
        max_concurrent: Max. parallele Redis-Ops (default: 50, tunable).
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        ttl_seconds: int = 3600,
        max_concurrent: int = 50,
    ) -> None:
        self.url = url
        self.default_ttl = ttl_seconds
        self._client: Redis | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent)

        log.info(
            "redis_adapter_init",
            url=url,
            default_ttl=ttl_seconds,
            max_concurrent=max_concurrent,
        )

    # --- Context Manager ---
    async def __aenter__(self) -> RedisAdapter:
        """Initialisiere Redis-Client (Connection-Pool)."""
        if self._client is None:
            self._client = await Redis.from_url(
                self.url,
                encoding="utf-8",
                decode_responses=False,  # wir serialisieren binär
            )
            # Health-Check: PING
            try:
                await self._client.ping()
                log.info("redis_connected", url=self.url)
            except RedisError as e:
                log.error("redis_connection_failed", url=self.url, error=str(e))
                raise
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Cleanup: schließe Redis-Connection-Pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            log.info("redis_closed")

    # --- CachePort-Implementierung ---
    async def get(self, key: str) -> Optional[Any]:
        """GET mit pickle-Deserialisierung."""
        if self._client is None:
            raise RuntimeError("Redis not initialized. Use 'async with cache:'")

        async with self._semaphore:
            try:
                raw = await self._client.get(key)
                if raw is None:
                    log.debug("cache_miss", key=key)
                    return None
                value = pickle.loads(raw)
                log.debug("cache_hit", key=key)
                return value
            except (RedisError, pickle.PickleError) as e:
                log.error("redis_get_error", key=key, error=str(e))
                return None

    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        """SET mit pickle-Serialisierung + TTL."""
        if self._client is None:
            raise RuntimeError("Redis not initialized.")

        expire_time = ttl if ttl is not None else self.default_ttl

        try:
            packed = pickle.dumps(value)
        except (pickle.PickleError, TypeError) as e:
            log.error("pickle_serialize_error", key=key, error=str(e))
            return

        async with self._semaphore:
            try:
                await self._client.setex(key, expire_time, packed)
                log.debug(
                    "cache_set",
                    key=key,
                    ttl=expire_time,
                    size_bytes=len(packed),
                )
            except RedisError as e:
                log.error("redis_set_error", key=key, error=str(e))

    async def delete(self, key: str) -> bool:
        """DEL Key."""
        if self._client is None:
            return False

        async with self._semaphore:
            try:
                deleted = await self._client.delete(key)
                log.debug("cache_delete", key=key, deleted=deleted > 0)
                return deleted > 0
            except RedisError as e:
                log.error("redis_delete_error", key=key, error=str(e))
                return False

    async def exists(self, key: str) -> bool:
        """EXISTS Check."""
        if self._client is None:
            return False

        async with self._semaphore:
            try:
                exists = await self._client.exists(key)
                return exists > 0
            except RedisError as e:
                log.error("redis_exists_error", key=key, error=str(e))
                return False

    async def clear(self) -> None:
        """FLUSHDB (lösche ALLE Keys in aktueller DB)."""
        if self._client is None:
            return

        async with self._semaphore:
            try:
                await self._client.flushdb()
                log.warning("redis_flushed")
            except RedisError as e:
                log.error("redis_flush_error", error=str(e))
