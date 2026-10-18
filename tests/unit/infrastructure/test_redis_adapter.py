"""Unit tests for RedisAdapter (client mocked; no Redis server)."""

from __future__ import annotations

import pickle
from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from structlog.testing import capture_logs

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


class TestOutage:
    """A Redis that cannot be reached logged an error on every call
    (325 000 ``redis_get_error``/``redis_set_error`` records in 24 h of a
    production outage): an outage is logged once, and its end once."""

    async def test_an_outage_is_logged_once_and_its_end_once(
        self, adapter: RedisAdapter, client: AsyncMock
    ) -> None:
        down = RedisConnectionError("Temporary failure in name resolution")
        client.get.side_effect = [down, down, pickle.dumps("v")]
        client.setex.side_effect = [down]

        with capture_logs() as logs:
            assert await adapter.get("a") is None
            await adapter.set("b", 1)
            assert await adapter.get("c") is None
            assert await adapter.get("d") == "v"

        unavailable = [e for e in logs if e["event"] == "redis_unavailable"]
        assert len(unavailable) == 1
        assert unavailable[0]["log_level"] == "error"
        assert unavailable[0]["error"] == "Temporary failure in name resolution"
        recovered = [e for e in logs if e["event"] == "redis_recovered"]
        assert len(recovered) == 1
        assert recovered[0]["log_level"] == "info"
        assert recovered[0]["failed"] == 3
        assert recovered[0]["seconds"] >= 0
        failures = [
            e for e in logs if e["event"] in ("redis_get_error", "redis_set_error")
        ]
        assert len(failures) == 3
        assert {e["log_level"] for e in failures} == {"debug"}

    async def test_a_second_outage_is_logged_again(
        self, adapter: RedisAdapter, client: AsyncMock
    ) -> None:
        down = RedisConnectionError("Connection refused")
        client.exists.side_effect = [down, 1, down, 1]

        with capture_logs() as logs:
            for _ in range(4):
                await adapter.exists("k")

        events = [
            e["event"]
            for e in logs
            if e["event"].startswith("redis_un") or e["event"] == "redis_recovered"
        ]
        assert events == [
            "redis_unavailable",
            "redis_recovered",
            "redis_unavailable",
            "redis_recovered",
        ]

    async def test_a_value_that_does_not_unpickle_is_no_outage(
        self, adapter: RedisAdapter, client: AsyncMock
    ) -> None:
        client.get.return_value = b"not a pickle"

        with capture_logs() as logs:
            assert await adapter.get("k") is None

        assert [e["event"] for e in logs if e["log_level"] == "error"] == [
            "redis_get_error"
        ]
