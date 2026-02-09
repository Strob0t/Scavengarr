"""Cache Infrastructure - Backend-Implementations."""

from .cache_factory import CacheBackend, create_cache
from .diskcache_adapter import DiskcacheAdapter
from .redis_adapter import RedisAdapter

__all__ = [
    "CacheBackend",
    "DiskcacheAdapter",
    "RedisAdapter",
    "create_cache",
]
