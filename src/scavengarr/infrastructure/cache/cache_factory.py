"""Cache-Factory - Erstellt Adapter basierend auf Config."""

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
    # Diskcache-Config
    directory: str = "./cache",
    # Redis-Config
    redis_url: str = "redis://localhost:6379/0",
    # Shared Config
    ttl_seconds: int = 3600,
    max_concurrent: int = 10,  # für diskcache, Redis hat höheres Limit (50)
) -> CachePort:
    """Factory-Funktion: Erstellt Cache-Adapter je nach Backend.

    Args:
        backend: "diskcache" (SQLite) oder "redis".
        directory: Diskcache-Pfad.
        redis_url: Redis-Connection-String.
        ttl_seconds: Standard-TTL für beide Backends.
        max_concurrent: Semaphore-Limit (diskcache: 10, Redis: 50 im Adapter).

    Returns:
        CachePort-Implementierung (DiskcacheAdapter oder RedisAdapter).

    Raises:
        ValueError: Wenn `backend` unbekannt.
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
            max_concurrent=50,  # Redis hat eigenes Limit
        )
        return RedisAdapter(
            url=redis_url,
            ttl_seconds=ttl_seconds,
            max_concurrent=50,  # Override für Redis
        )
    else:
        raise ValueError(
            f"Unknown cache backend: {backend!r}. Must be 'diskcache' or 'redis'."
        )
