"""Diskcache-Adapter - SQLite-basierter Cache ohne Daemon-Prozess."""

from __future__ import annotations

import asyncio
import pickle
from pathlib import Path
from typing import Any, Optional

import structlog
from diskcache import Cache as DiskCache

log = structlog.get_logger(__name__)


class DiskcacheAdapter:
    """Async-Wrapper für diskcache.Cache (sync-only Library).

    - Nutzt `asyncio.to_thread` für I/O (kein Blocking der Event-Loop).
    - Semaphore verhindert zu viele parallele Disk-Writes (SQLite Lock-Contention).
    - Implementiert Context-Manager (`async with`).

    Args:
        directory: SQLite-DB-Pfad (default: `./cache`).
        ttl_seconds: Standard-TTL für `set()` ohne explizite Angabe.
        max_concurrent: Max. parallele Disk-Ops (default: 10, tunable).
    """

    def __init__(
        self,
        directory: str | Path = "./cache",
        ttl_seconds: int = 3600,
        max_concurrent: int = 10,
    ) -> None:
        self.directory = Path(directory)
        self.default_ttl = ttl_seconds
        self._cache: DiskCache | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent)

        log.info(
            "diskcache_adapter_init",
            directory=str(self.directory),
            default_ttl=ttl_seconds,
            max_concurrent=max_concurrent,
        )

    # --- Context Manager ---
    async def __aenter__(self) -> DiskcacheAdapter:
        """Öffne SQLite-Cache (lazy, beim ersten Zugriff)."""
        if self._cache is None:
            self._cache = await asyncio.to_thread(
                DiskCache,
                str(self.directory),
            )
            log.info("diskcache_opened", path=str(self.directory))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Cleanup: schließe Cache, räume Locks auf."""
        await self.aclose()

    async def aclose(self) -> None:
        if self._cache is not None:
            await asyncio.to_thread(self._cache.close)
            self._cache = None
            log.info("diskcache_closed", directory=str(self.directory))

    # --- CachePort-Implementierung ---
    async def get(self, key: str) -> Optional[Any]:
        """Lese aus Cache (sync-Disk-I/O → to_thread)."""
        if self._cache is None:
            raise RuntimeError(
                "Cache not initialized. Use 'async with cache:' or await cache.__aenter__()"
            )

        async with self._semaphore:
            value = await asyncio.to_thread(self._cache.get, key, default=None)
            log.debug(
                "cache_get",
                key=key,
                hit=value is not None,
            )
            return value

    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        """Schreibe in Cache mit TTL (Standard: self.default_ttl)."""
        if self._cache is None:
            raise RuntimeError("Cache not initialized.")

        expire_time = ttl if ttl is not None else self.default_ttl

        async with self._semaphore:
            await asyncio.to_thread(
                self._cache.set,
                key,
                value,
                expire=expire_time,
            )
            log.debug(
                "cache_set",
                key=key,
                ttl=expire_time,
                size_bytes=len(pickle.dumps(value)),  # rough estimate
            )

    async def delete(self, key: str) -> bool:
        """Lösche Key. True = erfolgreich gelöscht."""
        if self._cache is None:
            return False

        async with self._semaphore:
            deleted = await asyncio.to_thread(self._cache.delete, key)
            log.debug("cache_delete", key=key, deleted=deleted)
            return deleted

    async def exists(self, key: str) -> bool:
        """Check ob Key existiert."""
        if self._cache is None:
            return False

        async with self._semaphore:
            # diskcache.Cache.__contains__ prüft Existenz + Expiry
            exists = await asyncio.to_thread(lambda: key in self._cache)
            return exists

    async def clear(self) -> None:
        """Lösche ALLE Keys."""
        if self._cache is None:
            return

        async with self._semaphore:
            await asyncio.to_thread(self._cache.clear)
            log.warning("cache_cleared", directory=str(self.directory))
