"""Cache Port - Interface for backend-agnostic caching strategies."""

from __future__ import annotations

from typing import Any, Protocol


class CachePort(Protocol):
    """Port for async key-value cache with TTL support.

    Implementations:
      - DiskcacheAdapter (SQLite-based, no daemon)
      - RedisAdapter (Redis async client)

    Each adapter MUST support async context-manager semantics:
        async with cache:
            await cache.set("key", value)
    """

    async def get(self, key: str) -> Any:
        """Retrieve value. None = not found / expired."""
        ...

    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        """Set value with optional TTL (seconds)."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete key. True = deleted, False = did not exist."""
        ...

    async def exists(self, key: str) -> bool:
        """Check if key exists (not expired)."""
        ...

    async def clear(self) -> None:
        """Delete ALL keys (e.g. for admin endpoint)."""
        ...

    async def aclose(self) -> None:
        """Cleanup hook (e.g. close Redis connection)."""
        ...

    # Context-Manager Support (optional, implemented by adapters)
    async def __aenter__(self) -> CachePort: ...

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...
