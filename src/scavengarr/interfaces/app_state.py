"""Application state container for FastAPI dependency injection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from starlette.datastructures import State

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.infrastructure.config import AppConfig

if TYPE_CHECKING:
    from scavengarr.application.use_cases.stremio_catalog import StremioCatalogUseCase
    from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
    from scavengarr.domain.ports import (
        CachePort,
        CrawlJobRepository,
        PluginRegistryPort,
        SearchEnginePort,
        StreamLinkRepository,
    )
    from scavengarr.domain.ports.tmdb import TmdbClientPort
    from scavengarr.infrastructure.hoster_resolvers import HosterResolverRegistry
    from scavengarr.infrastructure.hoster_resolvers.stealth_pool import StealthPool
    from scavengarr.infrastructure.metrics import MetricsCollector


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

    # Playwright Stealth pool (optional — for CF bypass probing)
    stealth_pool: StealthPool | None

    # Metrics (zero-impact in-memory counters)
    metrics: MetricsCollector

    # Stremio (optional — requires TMDB API key)
    tmdb_client: TmdbClientPort | None
    stremio_stream_uc: StremioStreamUseCase | None
    stremio_catalog_uc: StremioCatalogUseCase | None
