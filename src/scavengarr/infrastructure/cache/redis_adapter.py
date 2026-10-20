"""Redis-Adapter - Async Redis via redis.asyncio."""

from __future__ import annotations

import asyncio
import pickle
import time
from collections.abc import Callable
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

log = structlog.get_logger(__name__)


class RedisAdapter:
    """Async Redis cache with connection pooling via semaphore.

    - Uses `redis.asyncio.Redis` (async-native, no to_thread needed).
    - Semaphore limits parallel Redis ops (prevents connection exhaustion).
    - Serialization via pickle (consistent with Diskcache adapter).
    - An outage (Redis cannot be reached: a connection error or a timeout)
      is logged once (``redis_unavailable``, error, with the first
      failure's text) and its end once (``redis_recovered``, info, with
      the calls that failed and the outage's length); the failed calls in
      between log at debug. A production outage logged every call at
      error level: 325 000 records in 24 h.

    Args:
        url: Redis URL (e.g. `redis://localhost:6379/0`).
        ttl_seconds: Default TTL.
        max_concurrent: Max parallel Redis ops (default: 50, tunable).
        clock: Monotonic seconds, for the outage's length.
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        ttl_seconds: int = 3600,
        max_concurrent: int = 50,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self.default_ttl = ttl_seconds
        self._client: Redis | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._clock = clock
        # the running outage: its start and the calls that failed in it
        self._down_since: float | None = None
        self._failed = 0

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

    # --- Outages ---
    def _log_failure(self, event: str, error: RedisError, **fields: Any) -> None:
        """A failed call: at debug within an outage, whose start is logged
        once; any other Redis error at error level, as before."""
        if not isinstance(error, (RedisConnectionError, RedisTimeoutError)):
            log.error(event, error=str(error), **fields)
            return
        if self._down_since is None:
            self._down_since = self._clock()
            log.error("redis_unavailable", error=str(error))
        self._failed += 1
        log.debug(event, error=str(error), **fields)

    def _answered(self) -> None:
        """Redis answered: an outage ends, logged once."""
        if self._down_since is None:
            return
        log.info(
            "redis_recovered",
            failed=self._failed,
            seconds=round(self._clock() - self._down_since, 1),
        )
        self._down_since = None
        self._failed = 0

    # --- CachePort implementation ---
    async def get(self, key: str) -> Any | None:
        """GET with pickle deserialization."""
        if self._client is None:
            raise RuntimeError("Redis not initialized. Use 'async with cache:'")

        async with self._semaphore:
            try:
                raw = await self._client.get(key)
            except RedisError as e:
                self._log_failure("redis_get_error", e, key=key)
                return None
            self._answered()
            if raw is None:
                log.debug("cache_miss", key=key)
                return None
            if not isinstance(raw, bytes):
                # decode_responses=False: values this adapter stored are bytes
                log.warning("redis_get_not_bytes", key=key)
                return None
            try:
                value = pickle.loads(raw)
            except pickle.PickleError as e:
                log.error("redis_get_error", key=key, error=str(e))
                return None
            log.debug("cache_hit", key=key)
            return value

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
            except RedisError as e:
                self._log_failure("redis_set_error", e, key=key)
                return
            self._answered()
            log.debug("cache_set", key=key, ttl=expire_time, size_bytes=len(packed))

    async def delete(self, key: str) -> bool:
        """DEL Key."""
        if self._client is None:
            return False

        async with self._semaphore:
            try:
                deleted = await self._client.delete(key)
            except RedisError as e:
                self._log_failure("redis_delete_error", e, key=key)
                return False
            self._answered()
            log.debug("cache_delete", key=key, deleted=deleted > 0)
            return deleted > 0

    async def exists(self, key: str) -> bool:
        """EXISTS Check."""
        if self._client is None:
            return False

        async with self._semaphore:
            try:
                exists = await self._client.exists(key)
            except RedisError as e:
                self._log_failure("redis_exists_error", e, key=key)
                return False
            self._answered()
            return exists > 0

    async def ping(self) -> bool:
        """PING: whether Redis answers (the readiness probe's cache check);
        a failure counts toward the outage like any call's."""
        if self._client is None:
            return False

        async with self._semaphore:
            try:
                await self._client.ping()
            except RedisError as e:
                self._log_failure("redis_ping_error", e)
                return False
            self._answered()
            return True

    async def clear(self) -> None:
        """FLUSHDB (delete ALL keys in current DB)."""
        if self._client is None:
            return

        async with self._semaphore:
            try:
                await self._client.flushdb()
            except RedisError as e:
                self._log_failure("redis_flush_error", e)
                return
            self._answered()
            log.warning("redis_flushed")
