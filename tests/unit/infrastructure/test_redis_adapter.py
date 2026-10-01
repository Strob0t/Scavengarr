"""Unit tests for RedisAdapter (client mocked; no Redis server)."""

from __future__ import annotations

import pickle
from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis

from scavengarr.infrastructure.cache.redis_adapter import RedisAdapter


@pytest.fixture()
def client() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def adapter(client: AsyncMock) -> RedisAdapter:
    cache = RedisAdapter(url="redis://localhost:6379/0")
    cache._client = client
    return cache


class TestGet:
    async def test_unpickles_the_stored_bytes(
        self, adapter: RedisAdapter, client: AsyncMock
    ) -> None:
        client.get.return_value = pickle.dumps({"a": 1})
        assert await adapter.get("key") == {"a": 1}

    async def test_miss_returns_none(
        self, adapter: RedisAdapter, client: AsyncMock
    ) -> None:
        client.get.return_value = None
        assert await adapter.get("key") is None

    async def test_text_reply_is_a_miss(
        self, adapter: RedisAdapter, client: AsyncMock
    ) -> None:
        """The client runs with ``decode_responses=False``; a text reply
        (a key written by another client) is no pickled value."""
        client.get.return_value = "text"
        assert await adapter.get("key") is None


class TestConnect:
    async def test_pings_on_enter_and_closes_on_exit(
        self, client: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Redis, "from_url", AsyncMock(return_value=client))

        async with RedisAdapter() as cache:
            client.ping.assert_awaited_once()
            assert cache._client is client

        client.aclose.assert_awaited_once()
