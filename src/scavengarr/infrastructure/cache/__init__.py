"""Cache Infrastructure - Backend-Implementations."""

from .cache_factory import create_cache
from .diskcache_adapter import DiskcacheAdapter
from .redis_adapter import RedisAdapter

__all__ = [
    "DiskcacheAdapter",
    "RedisAdapter",
    "create_cache",
]
