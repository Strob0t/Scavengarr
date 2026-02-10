"""Cache factory - Creates adapter based on config."""

from __future__ import annotations

from typing import Literal

import structlog

from scavengarr.domain.ports.cache import CachePort
from scavengarr.infrastructure.cache.diskcache_adapter import DiskcacheAdapter
from scavengarr.infrastructure.cache.redis_adapter import RedisAdapter

log = structlog.get_logger(__name__)

CacheBackend = Literal["diskcache", "redis"]


def create_cache(
    backend: CacheBackend = "diskcache",
    *,
    # Diskcache config
    directory: str = "./cache",
    # Redis config
    redis_url: str = "redis://localhost:6379/0",
    # Shared config
    ttl_seconds: int = 3600,
    max_concurrent: int = 10,  # for diskcache; Redis has higher limit (50)
) -> CachePort:
    """Factory function: Creates cache adapter based on backend.

    Args:
        backend: "diskcache" (SQLite) or "redis".
        directory: Diskcache path.
        redis_url: Redis connection string.
        ttl_seconds: Default TTL for both backends.
        max_concurrent: Semaphore limit (diskcache: 10, Redis: 50 in adapter).

    Returns:
        CachePort implementation (DiskcacheAdapter or RedisAdapter).

    Raises:
        ValueError: If `backend` is unknown.
    """
    if backend == "diskcache":
        log.info(
            "cache_factory_create",
            backend=backend,
            directory=directory,
            ttl=ttl_seconds,
            max_concurrent=max_concurrent,
        )
        return DiskcacheAdapter(
            directory=directory,
            ttl_seconds=ttl_seconds,
            max_concurrent=max_concurrent,
        )
    elif backend == "redis":
        log.info(
            "cache_factory_create",
            backend=backend,
            url=redis_url,
            ttl=ttl_seconds,
            max_concurrent=50,  # Redis has its own limit
        )
        return RedisAdapter(
            url=redis_url,
            ttl_seconds=ttl_seconds,
            max_concurrent=50,  # Override for Redis
        )
    else:
        raise ValueError(
            f"Unknown cache backend: {backend!r}. Must be 'diskcache' or 'redis'."
        )
