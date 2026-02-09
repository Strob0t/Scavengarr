from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, cast

import httpx
import structlog
from fastapi import FastAPI

# CHANGED: Import CrawlJobFactory instead of CrawlJobService
from scavengarr.application.factories import CrawlJobFactory
from scavengarr.domain.entities.crawljob import Priority  # NEW: For factory config
from scavengarr.infrastructure.cache.factory import create_cache
from scavengarr.infrastructure.persistence.crawljob_cache import (
    CacheCrawlJobRepository,
)
from scavengarr.infrastructure.plugins import PluginRegistry
from scavengarr.infrastructure.torznab.httpx_scrapy_engine import (
    HttpxScrapySearchEngine,
)
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

    # ========== 1) Cache (MUST be first - other components depend on it) ==========
    cache = create_cache(
        backend=config.cache.backend,
        directory=str(config.cache.directory),
        redis_url=config.cache.redis_url,
        ttl_seconds=config.cache.ttl_seconds,
        max_concurrent=config.cache.max_concurrent,
    )

    await cache.__aenter__()  # Open cache (context manager)
    state.cache = cache
    log.info("cache_initialized", backend=config.cache.backend)

    # Dev-Mode: Clear cache on startup (optional)
    if config.environment == "dev":
        await cache.clear()
        log.debug("cache_cleared", environment="dev")

    # ========== 2) HTTP Client (shared resource) ==========
    state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(config.http_timeout_seconds),
        headers={"User-Agent": config.http_user_agent},
        follow_redirects=config.http_follow_redirects,
    )
    log.info("http_client_initialized")

    # ========== 3) Plugin Registry ==========
    state.plugins = PluginRegistry(plugin_dir=config.plugin_dir)
    state.plugins.discover()
    log.info("plugins_discovered", count=len(state.plugins.list_names()))

    # ========== 4) Search Engine (uses http_client + cache) ==========
    state.search_engine = HttpxScrapySearchEngine(
        http_client=state.http_client,
        cache=state.cache,
        validate_links=config.validate_download_links,
        validation_timeout=config.validation_timeout_seconds,
        validation_concurrency=config.validation_max_concurrent,
    )
    log.info("search_engine_initialized")

    # ========== 5) CrawlJob Repository (uses cache) ==========
    state.crawljob_repo = CacheCrawlJobRepository(
        cache=state.cache,
        ttl_seconds=3600,  # Job-specific TTL (1 hour)
    )
    log.info("crawljob_repo_initialized")

    # ========== 6) CrawlJob Factory (NEW - Phase 2) ==========
    # CHANGED: Replace CrawlJobService with CrawlJobFactory
    state.crawljob_factory = CrawlJobFactory(
        default_ttl_hours=1,  # CrawlJobs expire after 1 hour
        auto_start=True,  # Enable JDownloader auto-start by default
        default_priority=Priority.DEFAULT,  # Default download priority
    )
    log.info("crawljob_factory_initialized")

    log.info("app_startup_complete")

    try:
        yield  # ✅ App runs here
    finally:
        # ========== Cleanup (reverse order) ==========
        await state.http_client.aclose()
        log.info("http_client_closed")

        await state.cache.aclose()
        log.info("cache_closed")

        log.info("app_shutdown_complete")
