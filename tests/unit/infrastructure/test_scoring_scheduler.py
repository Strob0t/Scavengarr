"""Unit tests for ScoringScheduler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from scavengarr.domain.entities.scoring import (
    EwmaState,
    PluginScoreSnapshot,
    ProbeResult,
)
from scavengarr.infrastructure.config.schema import ScoringConfig
from scavengarr.infrastructure.scoring.scheduler import ScoringScheduler

_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_scheduler(
    *,
    plugin_names: list[str] | None = None,
    health_results: dict[str, ProbeResult] | None = None,
    search_probe: ProbeResult | None = None,
    stored_snapshot: PluginScoreSnapshot | None = None,
    last_health_run: datetime | None = None,
    last_search_run: datetime | None = None,
    config: ScoringConfig | None = None,
    query_titles: list[str] | None = None,
) -> ScoringScheduler:
    plugin_names = plugin_names or ["sto"]
    config = config or ScoringConfig()

    # Mock plugin registry (synchronous).
    registry = MagicMock()
    registry.get_by_provides.return_value = plugin_names
    plugin_mock = MagicMock()
    plugin_mock.base_url = "https://example.com"
    registry.get.return_value = plugin_mock

    # Mock health prober.
    health_prober = AsyncMock()
    if health_results is None:
        health_results = {
            name: ProbeResult(started_at=_NOW, duration_ms=100.0, ok=True)
            for name in plugin_names
        }
    health_prober.probe_all = AsyncMock(return_value=health_results)

    # Mock search prober.
    search_prober = AsyncMock()
    if search_probe is None:
        search_probe = ProbeResult(
            started_at=_NOW,
            duration_ms=200.0,
            ok=True,
            items_found=10,
        )
    search_prober.probe = AsyncMock(return_value=search_probe)

    # Mock query pool builder.
    query_pool = AsyncMock()
    query_pool.get_queries = AsyncMock(
        return_value=query_titles if query_titles is not None else ["Iron Man"]
    )

    # Mock score store.
    score_store = AsyncMock()
    score_store.get_snapshot = AsyncMock(return_value=stored_snapshot)

    async def _get_last_run(probe_type, plugin, category=None, bucket=None):
        if probe_type == "health":
            return last_health_run
        return last_search_run

    score_store.get_last_run = AsyncMock(side_effect=_get_last_run)
    score_store.put_snapshot = AsyncMock()
    score_store.set_last_run = AsyncMock()

    return ScoringScheduler(
        health_prober=health_prober,
        search_prober=search_prober,
        query_pool=query_pool,
        score_store=score_store,
        plugins=registry,
        config=config,
    )


class TestHealthCycle:
    async def test_runs_when_never_probed(self) -> None:
        scheduler = _make_scheduler(last_health_run=None)
        await scheduler._run_health_cycle(_NOW)

        scheduler._health.probe_all.assert_awaited_once()
        assert scheduler._store.put_snapshot.await_count > 0

    async def test_skips_when_recently_probed(self) -> None:
        recent = _NOW - timedelta(hours=1)
        scheduler = _make_scheduler(last_health_run=recent)
        await scheduler._run_health_cycle(_NOW)

        scheduler._health.probe_all.assert_not_awaited()

    async def test_runs_when_interval_elapsed(self) -> None:
        old = _NOW - timedelta(hours=25)
        scheduler = _make_scheduler(last_health_run=old)
        await scheduler._run_health_cycle(_NOW)

        scheduler._health.probe_all.assert_awaited_once()

    async def test_updates_all_category_bucket_combos(self) -> None:
        scheduler = _make_scheduler(last_health_run=None)
        await scheduler._run_health_cycle(_NOW)

        # 1 plugin × 2 categories × 3 buckets = 6 snapshots.
        assert scheduler._store.put_snapshot.await_count == 6

    async def test_sets_last_run_per_plugin(self) -> None:
        scheduler = _make_scheduler(
            plugin_names=["sto", "kinoger"],
            last_health_run=None,
        )
        await scheduler._run_health_cycle(_NOW)

        # set_last_run called once per plugin.
        calls = scheduler._store.set_last_run.call_args_list
        health_calls = [c for c in calls if c[0][0] == "health"]
        assert len(health_calls) == 2

    async def test_creates_new_snapshot_if_missing(self) -> None:
        scheduler = _make_scheduler(last_health_run=None, stored_snapshot=None)
        await scheduler._run_health_cycle(_NOW)

        # Snapshot should be created and stored.
        call = scheduler._store.put_snapshot.call_args_list[0]
        snap = call[0][0]
        assert snap.plugin == "sto"
        assert snap.health_score.n_samples == 1

    async def test_updates_existing_snapshot(self) -> None:
        existing = PluginScoreSnapshot(
            plugin="sto",
            category=2000,
            bucket="current",
            health_score=EwmaState(value=0.5, last_ts=_NOW, n_samples=5),
        )
        scheduler = _make_scheduler(last_health_run=None, stored_snapshot=existing)
        await scheduler._run_health_cycle(_NOW)

        call = scheduler._store.put_snapshot.call_args_list[0]
        snap = call[0][0]
        assert snap.health_score.n_samples == 6


class TestSearchCycle:
    async def test_runs_when_never_probed(self) -> None:
        scheduler = _make_scheduler(last_search_run=None)
        await scheduler._run_search_cycle(_NOW)

        scheduler._search.probe.assert_awaited()
        assert scheduler._store.put_snapshot.await_count > 0

    async def test_skips_when_recently_probed(self) -> None:
        recent = _NOW - timedelta(hours=1)
        scheduler = _make_scheduler(last_search_run=recent)
        await scheduler._run_search_cycle(_NOW)

        scheduler._search.probe.assert_not_awaited()

    async def test_queries_from_pool(self) -> None:
        scheduler = _make_scheduler(
            last_search_run=None,
            query_titles=["Test Film"],
        )
        await scheduler._run_search_cycle(_NOW)

        scheduler._query_pool.get_queries.assert_awaited()
        # Search prober called with the query from pool.
        call = scheduler._search.probe.call_args_list[0]
        assert call[0][1] == "Test Film"

    async def test_skips_when_no_queries(self) -> None:
        scheduler = _make_scheduler(last_search_run=None, query_titles=[])
        await scheduler._run_search_cycle(_NOW)

        scheduler._search.probe.assert_not_awaited()

    async def test_updates_search_score(self) -> None:
        scheduler = _make_scheduler(last_search_run=None)
        await scheduler._run_search_cycle(_NOW)

        call = scheduler._store.put_snapshot.call_args_list[0]
        snap = call[0][0]
        assert snap.search_score.n_samples == 1

    async def test_probes_all_category_bucket_combos(self) -> None:
        scheduler = _make_scheduler(last_search_run=None)
        await scheduler._run_search_cycle(_NOW)

        # 1 plugin × 2 categories × 3 buckets = 6.
        assert scheduler._search.probe.await_count == 6


class TestTick:
    async def test_tick_calls_both_cycles(self) -> None:
        scheduler = _make_scheduler(last_health_run=None)
        await scheduler._tick()

        scheduler._health.probe_all.assert_awaited()
        # Search also ran (never probed).
        assert scheduler._store.put_snapshot.await_count > 6
