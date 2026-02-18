"""Application state container for FastAPI dependency injection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from starlette.datastructures import State

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.config import AppConfig
from scavengarr.infrastructure.graceful_shutdown import GracefulShutdown

if TYPE_CHECKING:
    import asyncio

    from scavengarr.application.use_cases.stremio_catalog import StremioCatalogUseCase
    from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
    from scavengarr.domain.ports import (
        CachePort,
        CrawlJobRepository,
        PluginRegistryPort,
        PluginScoreStorePort,
        SearchEnginePort,
        StreamLinkRepository,
    )
    from scavengarr.domain.ports.tmdb import TmdbClientPort
    from scavengarr.infrastructure.concurrency import ConcurrencyPool
    from scavengarr.infrastructure.hoster_resolvers import HosterResolverRegistry
    from scavengarr.infrastructure.hoster_resolvers.stealth_pool import StealthPool
    from scavengarr.infrastructure.metrics import MetricsCollector
    from scavengarr.infrastructure.plugins.shared_browser import SharedBrowserPool
    from scavengarr.infrastructure.scoring.scheduler import ScoringScheduler


class AppState(State):
    """FastAPI application state with all DI resources.

    Lifecycle managed by composition.py::lifespan().
    """

    # Configuration
    config: AppConfig

    # Infrastructure
    cache: CachePort
    http_client: httpx.AsyncClient

    # Domain Ports
    plugins: PluginRegistryPort
    search_engine: SearchEnginePort
    crawljob_repo: CrawlJobRepository
    stream_link_repo: StreamLinkRepository

    # Application Services
    crawljob_factory: CrawlJobFactory

    # Hoster resolution
    hoster_resolver_registry: HosterResolverRegistry

    # Playwright shared browser pool (single Chromium for all PW plugins)
    shared_browser_pool: SharedBrowserPool | None

    # Playwright Stealth pool (optional — for CF bypass probing)
    stealth_pool: StealthPool | None

    # Metrics (zero-impact in-memory counters)
    metrics: MetricsCollector

    # Stremio (optional — requires TMDB API key)
    tmdb_client: TmdbClientPort | None
    stremio_stream_uc: StremioStreamUseCase | None
    stremio_catalog_uc: StremioCatalogUseCase | None

    # Global concurrency pool (fair-share httpx + PW slots)
    concurrency_pool: ConcurrencyPool | None

    # Circuit breaker (skip plugins after consecutive failures)
    circuit_breaker: PluginCircuitBreaker

    # Graceful shutdown (request tracking + drain)
    graceful_shutdown: GracefulShutdown

    # Plugin scoring (optional — requires scoring.enabled=True)
    plugin_score_store: PluginScoreStorePort | None
    scoring_scheduler: ScoringScheduler | None
    _scoring_task: asyncio.Task | None
