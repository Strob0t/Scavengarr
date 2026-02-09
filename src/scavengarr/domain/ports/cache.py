"""Cache Port - Interface für Backend-agnostische Caching-Strategien."""

from __future__ import annotations

from typing import Any, Optional, Protocol


class CachePort(Protocol):
    """Port für async Key-Value-Cache mit TTL-Support.

    Implementierungen:
      - DiskcacheAdapter (SQLite-basiert, kein Daemon)
      - RedisAdapter (Redis Async Client)

    Jeder Adapter MUSS async Context-Manager-Semantik unterstützen:
        async with cache:
            await cache.set("key", value)
    """

    async def get(self, key: str) -> Optional[Any]:
        """Rufe Wert ab. None = nicht vorhanden / expired."""
        ...

    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        """Setze Wert mit optionalem TTL (Sekunden)."""
        ...

    async def delete(self, key: str) -> bool:
        """Lösche Key. True = gelöscht, False = existierte nicht."""
        ...

    async def exists(self, key: str) -> bool:
        """Check ob Key existiert (nicht expired)."""
        ...

    async def clear(self) -> None:
        """Lösche ALLE Keys (z. B. für Admin-Endpoint)."""
        ...

    async def aclose(self) -> None:
        """Cleanup-Hook (z. B. Redis Connection schließen)."""
        ...

    # Context-Manager Support (optional, wird von Adaptern implementiert)
    async def __aenter__(self) -> CachePort: ...

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...
