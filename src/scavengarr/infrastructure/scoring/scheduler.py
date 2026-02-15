"""Background scoring scheduler — runs health and search probe cycles."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import structlog

from scavengarr.domain.entities.scoring import (
    AgeBucket,
    PluginScoreSnapshot,
)
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.plugin_score_store import PluginScoreStorePort
from scavengarr.infrastructure.config.schema import ScoringConfig
from scavengarr.infrastructure.scoring.ewma import (
    alpha_from_halflife,
    compute_health_observation,
    compute_search_observation,
    ewma_update,
    update_snapshot_scores,
)
from scavengarr.infrastructure.scoring.health_prober import HealthProber
from scavengarr.infrastructure.scoring.query_pool import QueryPoolBuilder
from scavengarr.infrastructure.scoring.search_prober import MiniSearchProber

log = structlog.get_logger(__name__)

_AGE_BUCKETS: list[AgeBucket] = ["current", "y1_2", "y5_10"]

# Standard Torznab top-level categories to probe.
_PROBE_CATEGORIES: list[int] = [2000, 5000]


class ScoringScheduler:
    """Runs periodic health and search probes in the background.

    Call :meth:`run_forever` as an asyncio task during app lifespan.
    Cancellation is clean — the task awaits its current sleep and exits.
    """

    def __init__(
        self,
        *,
        health_prober: HealthProber,
        search_prober: MiniSearchProber,
        query_pool: QueryPoolBuilder,
        score_store: PluginScoreStorePort,
        plugins: PluginRegistryPort,
        config: ScoringConfig,
    ) -> None:
        self._health = health_prober
        self._search = search_prober
        self._query_pool = query_pool
        self._store = score_store
        self._plugins = plugins
        self._config = config

    async def run_forever(self) -> None:
        """Main loop: check due probes, execute, sleep, repeat."""
        log.info("scoring_scheduler_started")
        try:
            # Initial delay to let plugins finish loading.
            await asyncio.sleep(10)

            while True:
                try:
                    await self._tick()
                except Exception:
                    log.error(
                        "scoring_scheduler_tick_error",
                        exc_info=True,
                    )
                # Sleep 5 minutes between ticks.
                await asyncio.sleep(300)
        except asyncio.CancelledError:
            log.info("scoring_scheduler_cancelled")
            raise

    async def _tick(self) -> None:
        """Run one scheduler tick — check and execute due probes."""
        now = datetime.now(timezone.utc)
        await self._run_health_cycle(now)
        await self._run_search_cycle(now)

    async def _run_health_cycle(self, now: datetime) -> None:
        """Run health probes for plugins that are due."""
        interval = timedelta(hours=self._config.health_interval_hours)
        stream_plugins = self._plugins.get_by_provides("stream")
        due: dict[str, str] = {}

        for name in stream_plugins:
            last = await self._store.get_last_run("health", name)
            if last is None or (now - last) >= interval:
                plugin = self._plugins.get(name)
                due[name] = getattr(plugin, "base_url", "")

        if not due:
            return

        log.info("health_cycle_start", plugins=len(due))
        results = await self._health.probe_all(
            due, concurrency=self._config.health_concurrency
        )

        alpha = alpha_from_halflife(
            self._config.health_interval_hours / 24.0,
            self._config.health_halflife_days,
        )

        for name, probe in results.items():
            obs = compute_health_observation(probe)
            # Update health EWMA for all category/bucket combos.
            for cat in _PROBE_CATEGORIES:
                for bucket in _AGE_BUCKETS:
                    snap = await self._store.get_snapshot(
                        name, cat, bucket
                    )
                    if snap is None:
                        snap = PluginScoreSnapshot(
                            plugin=name,
                            category=cat,
                            bucket=bucket,
                        )
                    new_health = ewma_update(
                        snap.health_score, obs, alpha, now
                    )
                    score, conf = update_snapshot_scores(
                        new_health,
                        snap.search_score,
                        w_health=self._config.w_health,
                        w_search=self._config.w_search,
                        now=now,
                    )
                    updated = replace(
                        snap,
                        health_score=new_health,
                        final_score=score,
                        confidence=conf,
                        updated_at=now,
                    )
                    await self._store.put_snapshot(updated)

            await self._store.set_last_run("health", name, now)

        log.info(
            "health_cycle_done",
            probed=len(results),
            ok=sum(1 for r in results.values() if r.ok),
        )

    async def _run_search_cycle(self, now: datetime) -> None:
        """Run search probes for plugins/categories/buckets that are due."""
        search_interval = timedelta(
            days=7.0 / max(self._config.search_runs_per_week, 1)
        )
        stream_plugins = self._plugins.get_by_provides("stream")
        sem = asyncio.Semaphore(self._config.search_concurrency)
        probed = 0

        for name in stream_plugins:
            for cat in _PROBE_CATEGORIES:
                for bucket in _AGE_BUCKETS:
                    last = await self._store.get_last_run(
                        "search", name, cat, bucket
                    )
                    if last is not None and (now - last) < search_interval:
                        continue

                    queries = await self._query_pool.get_queries(
                        cat, bucket, count=1
                    )
                    if not queries:
                        continue

                    async with sem:
                        probe = await self._search.probe(
                            name,
                            queries[0],
                            cat,
                            max_items=self._config.search_max_items,
                            timeout=self._config.search_timeout_seconds,
                        )

                    obs = compute_search_observation(
                        probe, self._config.search_max_items
                    )
                    alpha = alpha_from_halflife(
                        7.0 / max(self._config.search_runs_per_week, 1),
                        self._config.search_halflife_weeks * 7.0,
                    )

                    snap = await self._store.get_snapshot(
                        name, cat, bucket
                    )
                    if snap is None:
                        snap = PluginScoreSnapshot(
                            plugin=name, category=cat, bucket=bucket
                        )
                    new_search = ewma_update(
                        snap.search_score, obs, alpha, now
                    )
                    score, conf = update_snapshot_scores(
                        snap.health_score,
                        new_search,
                        w_health=self._config.w_health,
                        w_search=self._config.w_search,
                        now=now,
                    )
                    updated = replace(
                        snap,
                        search_score=new_search,
                        final_score=score,
                        confidence=conf,
                        updated_at=now,
                    )
                    await self._store.put_snapshot(updated)
                    await self._store.set_last_run(
                        "search", name, now, cat, bucket
                    )
                    probed += 1

                    log.debug(
                        "search_probe_done",
                        plugin=name,
                        category=cat,
                        bucket=bucket,
                        ok=probe.ok,
                        items=probe.items_found,
                    )

        if probed:
            log.info("search_cycle_done", probed=probed)
