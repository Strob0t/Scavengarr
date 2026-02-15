"""Unit tests for CachePluginScoreStore."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from scavengarr.domain.entities.scoring import EwmaState, PluginScoreSnapshot
from scavengarr.infrastructure.persistence.plugin_score_cache import (
    CachePluginScoreStore,
    _serialize_snapshot,
)

_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_snapshot(
    *,
    plugin: str = "sto",
    category: int = 5000,
    bucket: str = "current",
) -> PluginScoreSnapshot:
    return PluginScoreSnapshot(
        plugin=plugin,
        category=category,
        bucket=bucket,
        health_score=EwmaState(value=0.8, last_ts=_NOW, n_samples=10),
        search_score=EwmaState(value=0.7, last_ts=_NOW, n_samples=5),
        final_score=0.74,
        confidence=0.85,
        updated_at=_NOW,
    )


class TestGetSnapshot:
    async def test_returns_snapshot_when_cached(
        self, mock_cache: AsyncMock
    ) -> None:
        snap = _make_snapshot()
        mock_cache.get = AsyncMock(return_value=_serialize_snapshot(snap))
        store = CachePluginScoreStore(cache=mock_cache)

        result = await store.get_snapshot("sto", 5000, "current")

        assert result is not None
        assert result.plugin == "sto"
        assert result.category == 5000
        assert result.bucket == "current"
        assert result.final_score == pytest.approx(0.74)
        assert result.health_score.value == pytest.approx(0.8)
        assert result.search_score.n_samples == 5

    async def test_returns_none_when_missing(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        store = CachePluginScoreStore(cache=mock_cache)

        result = await store.get_snapshot("sto", 5000, "current")
        assert result is None

    async def test_returns_none_on_corrupt_data(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value="not-valid-json{{{")
        store = CachePluginScoreStore(cache=mock_cache)

        result = await store.get_snapshot("sto", 5000, "current")
        assert result is None

    async def test_uses_correct_key(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        store = CachePluginScoreStore(cache=mock_cache)

        await store.get_snapshot("kinoger", 2000, "y1_2")
        mock_cache.get.assert_awaited_once_with("score:kinoger:2000:y1_2")


class TestPutSnapshot:
    async def test_stores_serialized_json(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        snap = _make_snapshot()
        store = CachePluginScoreStore(cache=mock_cache)

        await store.put_snapshot(snap)

        # First call is the snapshot set, second is the index set.
        snapshot_call = mock_cache.set.call_args_list[0]
        key = snapshot_call[0][0]
        value = snapshot_call[0][1]
        assert key == "score:sto:5000:current"
        restored = json.loads(value)
        assert restored["plugin"] == "sto"
        assert restored["final_score"] == pytest.approx(0.74)

    async def test_uses_configured_ttl(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        snap = _make_snapshot()
        store = CachePluginScoreStore(cache=mock_cache, ttl_days=15)

        await store.put_snapshot(snap)

        snapshot_call = mock_cache.set.call_args_list[0]
        assert snapshot_call[1]["ttl"] == 15 * 86_400

    async def test_default_ttl_is_30_days(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        snap = _make_snapshot()
        store = CachePluginScoreStore(cache=mock_cache)

        await store.put_snapshot(snap)

        snapshot_call = mock_cache.set.call_args_list[0]
        assert snapshot_call[1]["ttl"] == 30 * 86_400

    async def test_updates_index(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        snap = _make_snapshot()
        store = CachePluginScoreStore(cache=mock_cache)

        await store.put_snapshot(snap)

        # Index write is the second set call.
        index_call = mock_cache.set.call_args_list[1]
        key = index_call[0][0]
        value = json.loads(index_call[0][1])
        assert key == "score:_index"
        assert ["sto", 5000, "current"] in value

    async def test_does_not_duplicate_index_entry(
        self, mock_cache: AsyncMock
    ) -> None:
        existing_index = json.dumps([["sto", 5000, "current"]])
        # _load_index calls cache.get(_INDEX_KEY) — return existing index.
        mock_cache.get = AsyncMock(return_value=existing_index)
        snap = _make_snapshot()
        store = CachePluginScoreStore(cache=mock_cache)

        await store.put_snapshot(snap)

        # Only 1 set call (snapshot itself) — no index update needed.
        assert mock_cache.set.call_count == 1


class TestListSnapshots:
    async def test_returns_all_snapshots(
        self, mock_cache: AsyncMock
    ) -> None:
        snap_a = _make_snapshot(plugin="sto")
        snap_b = _make_snapshot(plugin="kinoger", category=2000)
        index = json.dumps([
            ["sto", 5000, "current"],
            ["kinoger", 2000, "current"],
        ])
        mock_cache.get = AsyncMock(
            side_effect=[
                index,
                _serialize_snapshot(snap_a),
                _serialize_snapshot(snap_b),
            ]
        )
        store = CachePluginScoreStore(cache=mock_cache)

        results = await store.list_snapshots()
        assert len(results) == 2
        assert results[0].plugin == "sto"
        assert results[1].plugin == "kinoger"

    async def test_filters_by_plugin(self, mock_cache: AsyncMock) -> None:
        snap_a = _make_snapshot(plugin="sto")
        index = json.dumps([
            ["sto", 5000, "current"],
            ["kinoger", 2000, "current"],
        ])
        mock_cache.get = AsyncMock(
            side_effect=[
                index,
                _serialize_snapshot(snap_a),
            ]
        )
        store = CachePluginScoreStore(cache=mock_cache)

        results = await store.list_snapshots(plugin="sto")
        assert len(results) == 1
        assert results[0].plugin == "sto"

    async def test_returns_empty_when_no_index(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        store = CachePluginScoreStore(cache=mock_cache)

        results = await store.list_snapshots()
        assert results == []

    async def test_skips_expired_snapshots(
        self, mock_cache: AsyncMock
    ) -> None:
        index = json.dumps([["sto", 5000, "current"]])
        # Index exists but snapshot has expired (returns None).
        mock_cache.get = AsyncMock(side_effect=[index, None])
        store = CachePluginScoreStore(cache=mock_cache)

        results = await store.list_snapshots()
        assert results == []


class TestLastRun:
    async def test_set_and_get_last_run(
        self, mock_cache: AsyncMock
    ) -> None:
        store = CachePluginScoreStore(cache=mock_cache)
        await store.set_last_run("health", "sto", _NOW)

        mock_cache.set.assert_awaited_once()
        call = mock_cache.set.call_args
        assert call[0][0] == "lastrun:health:sto"
        assert call[0][1] == _NOW.isoformat()

    async def test_get_last_run_returns_datetime(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=_NOW.isoformat())
        store = CachePluginScoreStore(cache=mock_cache)

        result = await store.get_last_run("health", "sto")
        assert result == _NOW

    async def test_get_last_run_returns_none_when_missing(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        store = CachePluginScoreStore(cache=mock_cache)

        result = await store.get_last_run("search", "sto")
        assert result is None

    async def test_get_last_run_handles_corrupt_data(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value="not-a-date")
        store = CachePluginScoreStore(cache=mock_cache)

        result = await store.get_last_run("health", "sto")
        assert result is None

    async def test_key_includes_category_and_bucket(
        self, mock_cache: AsyncMock
    ) -> None:
        store = CachePluginScoreStore(cache=mock_cache)
        await store.set_last_run("search", "sto", _NOW, category=5000, bucket="y1_2")

        call = mock_cache.set.call_args
        assert call[0][0] == "lastrun:search:sto:5000:y1_2"

    async def test_key_without_category_and_bucket(
        self, mock_cache: AsyncMock
    ) -> None:
        store = CachePluginScoreStore(cache=mock_cache)
        await store.set_last_run("health", "kinoger", _NOW)

        call = mock_cache.set.call_args
        assert call[0][0] == "lastrun:health:kinoger"
