"""Composition root: dependency injection via FastAPI lifespan."""

from __future__ import annotations

import functools
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, cast

import httpx
import structlog
from fastapi import FastAPI

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.application.use_cases.stremio_catalog import StremioCatalogUseCase
from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
from scavengarr.domain.entities.crawljob import Priority
from scavengarr.infrastructure.cache.cache_factory import create_cache
from scavengarr.infrastructure.hoster_resolvers import HosterResolverRegistry
from scavengarr.infrastructure.hoster_resolvers.alfafile import AlfafileResolver
from scavengarr.infrastructure.hoster_resolvers.alphaddl import AlphaddlResolver
from scavengarr.infrastructure.hoster_resolvers.ddownload import DDownloadResolver
from scavengarr.infrastructure.hoster_resolvers.doodstream import DoodStreamResolver
from scavengarr.infrastructure.hoster_resolvers.fastpic import FastpicResolver
from scavengarr.infrastructure.hoster_resolvers.filecrypt import FilecryptResolver
from scavengarr.infrastructure.hoster_resolvers.filefactory import FilefactoryResolver
from scavengarr.infrastructure.hoster_resolvers.filemoon import FilemoonResolver
from scavengarr.infrastructure.hoster_resolvers.filernet import FilerNetResolver
from scavengarr.infrastructure.hoster_resolvers.fsst import FsstResolver
from scavengarr.infrastructure.hoster_resolvers.go4up import Go4upResolver
from scavengarr.infrastructure.hoster_resolvers.mixdrop import MixdropResolver
from scavengarr.infrastructure.hoster_resolvers.nitroflare import NitroflareResolver
from scavengarr.infrastructure.hoster_resolvers.onefichier import OnefichierResolver
from scavengarr.infrastructure.hoster_resolvers.probe import probe_urls_stealth
from scavengarr.infrastructure.hoster_resolvers.rapidgator import RapidgatorResolver
from scavengarr.infrastructure.hoster_resolvers.serienstream import SerienstreamResolver
from scavengarr.infrastructure.hoster_resolvers.stealth_pool import StealthPool
from scavengarr.infrastructure.hoster_resolvers.stmix import StmixResolver
from scavengarr.infrastructure.hoster_resolvers.streamtape import StreamtapeResolver
from scavengarr.infrastructure.hoster_resolvers.supervideo import SuperVideoResolver
from scavengarr.infrastructure.hoster_resolvers.turbobit import TurbobitResolver
from scavengarr.infrastructure.hoster_resolvers.uploaded import UploadedResolver
from scavengarr.infrastructure.hoster_resolvers.vidguard import VidguardResolver
from scavengarr.infrastructure.hoster_resolvers.vidking import VidkingResolver
from scavengarr.infrastructure.hoster_resolvers.voe import VoeResolver
from scavengarr.infrastructure.hoster_resolvers.xfs import create_all_xfs_resolvers
from scavengarr.infrastructure.metrics import MetricsCollector
from scavengarr.infrastructure.persistence.crawljob_cache import (
    CacheCrawlJobRepository,
)
from scavengarr.infrastructure.persistence.stream_link_cache import (
    CacheStreamLinkRepository,
)
from scavengarr.infrastructure.plugins import PluginRegistry
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.tmdb.client import HttpxTmdbClient
from scavengarr.infrastructure.tmdb.imdb_fallback import ImdbFallbackClient
from scavengarr.infrastructure.torznab.search_engine import HttpxSearchEngine
from scavengarr.interfaces.app_state import AppState

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan Hook: Initialize and cleanup all resources (DI Composition Root).

    Order matters:
        1. Cache (required by other components)
        2. HTTP Client (required by search engine)
        3. Plugin Registry
        4. Search Engine (uses HTTP client + cache)
        5. CrawlJob Repository (uses cache)
        6. CrawlJob Factory (stateless, no dependencies)
    """
    state = cast(AppState, app.state)
    config = state.config

    # 0) Metrics collector (zero-overhead, must exist before components that record)
    state.metrics = MetricsCollector()

    # 0b) Auto-tune max_concurrent_plugins based on host capacity
    if config.stremio.max_concurrent_plugins_auto:
        cpu_count = os.cpu_count() or 2
        try:
            import psutil

            available_ram_gb = psutil.virtual_memory().available / (1024**3)
            mem_limit = int(available_ram_gb * 2)
        except ImportError:
            mem_limit = 8  # conservative default without psutil

        auto_concurrent = max(2, min(cpu_count, mem_limit, 10))
        config.stremio.max_concurrent_plugins = auto_concurrent
        log.info(
            "auto_concurrency",
            cpu=cpu_count,
            mem_limit=mem_limit,
            result=auto_concurrent,
        )

    # 1) Cache (must be first - other components depend on it)
    cache = create_cache(
        backend=config.cache.backend,
        directory=str(config.cache.directory),
        redis_url=config.cache.redis_url,
        ttl_seconds=config.cache.ttl_seconds,
        max_concurrent=config.cache.max_concurrent,
    )

    await cache.__aenter__()
    state.cache = cache
    log.info("cache_initialized", backend=config.cache.backend)

    if config.environment == "dev":
        await cache.clear()
        log.debug("cache_cleared", environment="dev")

    # 2) HTTP client
    state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(config.http_timeout_seconds),
        headers={"User-Agent": config.http_user_agent},
        follow_redirects=config.http_follow_redirects,
    )
    log.info("http_client_initialized")

    # 2b) Share HTTP client with httpx-based plugins
    HttpxPluginBase.set_shared_http_client(state.http_client)

    # 3) Plugin registry
    state.plugins = PluginRegistry(plugin_dir=config.plugin_dir)
    state.plugins.discover()
    log.info("plugins_discovered", count=state.plugins.discovered_count)

    # 4) Search engine
    state.search_engine = HttpxSearchEngine(
        http_client=state.http_client,
        cache=state.cache,
        validate_links=config.validate_download_links,
        validation_timeout=config.validation_timeout_seconds,
        validation_concurrency=config.validation_max_concurrent,
    )
    log.info("search_engine_initialized")

    # 5) CrawlJob repository
    state.crawljob_repo = CacheCrawlJobRepository(
        cache=state.cache,
        ttl_seconds=3600,
    )
    log.info("crawljob_repo_initialized")

    # 6) CrawlJob factory
    state.crawljob_factory = CrawlJobFactory(
        default_ttl_hours=1,
        auto_start=True,
        default_priority=Priority.DEFAULT,
    )
    log.info("crawljob_factory_initialized")

    # 7) TMDB client (with IMDB fallback when no API key is configured)
    if config.tmdb_api_key:
        state.tmdb_client = HttpxTmdbClient(
            api_key=config.tmdb_api_key,
            http_client=state.http_client,
            cache=state.cache,
        )
        log.info("tmdb_client_initialized")
    else:
        state.tmdb_client = ImdbFallbackClient(
            http_client=state.http_client,
            cache=state.cache,
        )
        log.info(
            "tmdb_client_fallback",
            reason="no API key, using IMDB suggest API",
        )

    # 8) Hoster resolver registry (for extracting video URLs from embed pages)
    state.hoster_resolver_registry = HosterResolverRegistry(
        resolvers=[
            # Streaming resolvers (extract direct video URLs)
            VoeResolver(http_client=state.http_client),
            StreamtapeResolver(http_client=state.http_client),
            SuperVideoResolver(
                http_client=state.http_client,
                playwright_headless=config.playwright_headless,
                playwright_timeout_ms=config.playwright_timeout_ms,
            ),
            DoodStreamResolver(http_client=state.http_client),
            FilemoonResolver(http_client=state.http_client),
            # DDL resolvers (non-XFS)
            FilerNetResolver(http_client=state.http_client),
            RapidgatorResolver(http_client=state.http_client),
            DDownloadResolver(http_client=state.http_client),
            AlfafileResolver(http_client=state.http_client),
            AlphaddlResolver(http_client=state.http_client),
            FastpicResolver(http_client=state.http_client),
            FilecryptResolver(http_client=state.http_client),
            FilefactoryResolver(http_client=state.http_client),
            FsstResolver(http_client=state.http_client),
            Go4upResolver(http_client=state.http_client),
            MixdropResolver(http_client=state.http_client),
            NitroflareResolver(http_client=state.http_client),
            OnefichierResolver(http_client=state.http_client),
            SerienstreamResolver(http_client=state.http_client),
            StmixResolver(http_client=state.http_client),
            TurbobitResolver(http_client=state.http_client),
            UploadedResolver(http_client=state.http_client),
            VidguardResolver(http_client=state.http_client),
            VidkingResolver(http_client=state.http_client),
            # XFS resolvers (consolidated — 15 hosters)
            *create_all_xfs_resolvers(http_client=state.http_client),
        ],
        http_client=state.http_client,
        resolve_timeout=config.http_timeout_resolve_seconds,
    )
    log.info(
        "hoster_resolver_registry_initialized",
        hosters=state.hoster_resolver_registry.supported_hosters,
    )

    # 9) Stream link repository (for Stremio play endpoint)
    state.stream_link_repo = CacheStreamLinkRepository(
        cache=state.cache,
        ttl_seconds=config.stremio.stream_link_ttl_seconds,
    )
    log.info(
        "stream_link_repo_initialized",
        ttl_seconds=config.stremio.stream_link_ttl_seconds,
    )

    # 10) Stealth pool (optional — for Cloudflare bypass probing)
    if config.stremio.probe_stealth_enabled:
        state.stealth_pool = StealthPool(
            headless=config.playwright_headless,
            timeout_ms=int(config.stremio.probe_stealth_timeout_seconds * 1000),
        )
        log.info("stealth_pool_configured")
    else:
        state.stealth_pool = None

    # 11) Stremio use cases (always initialized — fallback handles missing key)
    probe_fn = functools.partial(
        probe_urls_stealth,
        state.http_client,
        stealth_pool=state.stealth_pool,
        concurrency=config.stremio.probe_concurrency,
        stealth_concurrency=config.stremio.probe_stealth_concurrency,
        timeout=config.stremio.probe_timeout_seconds,
        stealth_timeout=config.stremio.probe_stealth_timeout_seconds,
    )
    state.stremio_stream_uc = StremioStreamUseCase(
        tmdb=state.tmdb_client,
        plugins=state.plugins,
        search_engine=state.search_engine,
        config=config.stremio,
        stream_link_repo=state.stream_link_repo,
        probe_fn=probe_fn,
        resolve_fn=state.hoster_resolver_registry.resolve,
        metrics=state.metrics,
    )
    state.stremio_catalog_uc = StremioCatalogUseCase(tmdb=state.tmdb_client)

    log.info("app_startup_complete")

    try:
        yield
    finally:
        if state.stealth_pool is not None:
            await state.stealth_pool.cleanup()
            log.info("stealth_pool_cleaned_up")

        await state.hoster_resolver_registry.cleanup()
        log.info("hoster_resolvers_cleaned_up")

        await state.http_client.aclose()
        log.info("http_client_closed")

        await state.cache.aclose()
        log.info("cache_closed")

        log.info("app_shutdown_complete")
