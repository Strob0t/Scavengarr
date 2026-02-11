"""Composition root: dependency injection via FastAPI lifespan."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, cast

import httpx
import structlog
from fastapi import FastAPI

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.domain.entities.crawljob import Priority
from scavengarr.infrastructure.cache.cache_factory import create_cache
from scavengarr.infrastructure.persistence.crawljob_cache import (
    CacheCrawlJobRepository,
)
from scavengarr.infrastructure.plugins import PluginRegistry
from scavengarr.infrastructure.tmdb.client import HttpxTmdbClient
from scavengarr.infrastructure.torznab.search_engine import HttpxScrapySearchEngine
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

    # 3) Plugin registry
    state.plugins = PluginRegistry(plugin_dir=config.plugin_dir)
    state.plugins.discover()
    log.info("plugins_discovered", count=len(state.plugins.list_names()))

    # 4) Search engine
    state.search_engine = HttpxScrapySearchEngine(
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

    # 7) TMDB client (optional — requires API key for Stremio addon)
    if config.tmdb_api_key:
        state.tmdb_client = HttpxTmdbClient(
            api_key=config.tmdb_api_key,
            http_client=state.http_client,
            cache=state.cache,
        )
        log.info("tmdb_client_initialized")
    else:
        state.tmdb_client = None
        log.info("tmdb_client_skipped", reason="no API key configured")

    log.info("app_startup_complete")

    try:
        yield
    finally:
        await state.http_client.aclose()
        log.info("http_client_closed")

        await state.cache.aclose()
        log.info("cache_closed")

        log.info("app_shutdown_complete")
