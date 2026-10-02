"""Tests for the cache backend factory."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scavengarr.infrastructure.cache import cache_factory
from scavengarr.infrastructure.cache.cache_factory import create_cache


def test_redis_gets_the_configured_max_concurrent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cache.max_concurrent applies to Redis too (was hard-coded to 50)."""
    adapter = MagicMock()
    monkeypatch.setattr(cache_factory, "RedisAdapter", adapter)

    create_cache(
        "redis", redis_url="redis://redis:6379/0", ttl_seconds=60, max_concurrent=7
    )

    adapter.assert_called_once_with(
        url="redis://redis:6379/0", ttl_seconds=60, max_concurrent=7
    )


def test_diskcache_gets_the_configured_max_concurrent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = MagicMock()
    monkeypatch.setattr(cache_factory, "DiskcacheAdapter", adapter)

    create_cache("diskcache", directory="/tmp/c", ttl_seconds=60, max_concurrent=7)

    adapter.assert_called_once_with(
        directory="/tmp/c", ttl_seconds=60, max_concurrent=7
    )
