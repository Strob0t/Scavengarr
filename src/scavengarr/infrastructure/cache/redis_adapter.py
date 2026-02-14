"""Redis-Adapter - Async Redis via redis.asyncio."""

from __future__ import annotations

import asyncio
import pickle
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

log = structlog.get_logger(__name__)


class RedisAdapter:
    """Async Redis cache with connection pooling via semaphore.

    - Uses `redis.asyncio.Redis` (async-native, no to_thread needed).
    - Semaphore limits parallel Redis ops (prevents connection exhaustion).
    - Serialization via pickle (consistent with Diskcache adapter).

    Args:
        url: Redis URL (e.g. `redis://localhost:6379/0`).
        ttl_seconds: Default TTL.
        max_concurrent: Max parallel Redis ops (default: 50, tunable).
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
        """Initialize Redis client (connection pool)."""
        if self._client is None:
            self._client = await Redis.from_url(
                self.url,
                encoding="utf-8",
                decode_responses=False,  # we serialize binary
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
        """Cleanup: close Redis connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            log.info("redis_closed")

    # --- CachePort implementation ---
    async def get(self, key: str) -> Any | None:
        """GET with pickle deserialization."""
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
        """SET with pickle serialization + TTL."""
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
        """FLUSHDB (delete ALL keys in current DB)."""
        if self._client is None:
            return

        async with self._semaphore:
            try:
                await self._client.flushdb()
                log.warning("redis_flushed")
            except RedisError as e:
                log.error("redis_flush_error", error=str(e))
