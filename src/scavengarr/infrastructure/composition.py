from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, cast

import httpx
import structlog
from diskcache import Cache
from fastapi import FastAPI

from scavengarr.domain.ports import PluginRegistryPort, SearchEnginePort
from scavengarr.infrastructure.config import AppConfig
from scavengarr.infrastructure.plugins import PluginRegistry
from scavengarr.infrastructure.torznab.httpx_scrapy_engine import (
    HttpxScrapySearchEngine,
)
from scavengarr.interfaces.app_state import AppState

# Lazy import nur für Type Hints
if TYPE_CHECKING:
    from fastapi import FastAPI

    from scavengarr.interfaces.app_state import AppState

log = structlog.get_logger(__name__)


class DependencyContainer:
    """Lazy-loaded Dependency Container."""

    def __init__(self, config: AppConfig, state: AppState) -> None:
        self.config = config
        self.state = state
        self._http_client: httpx.AsyncClient | None = None
        self._plugin_registry: PluginRegistryPort | None = None
        self._search_engine: SearchEnginePort | None = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.http_timeout_seconds),
                headers={"User-Agent": self.config.http_user_agent},
                follow_redirects=self.config.http_follow_redirects,
            )
        return self._http_client

    @property
    def plugin_registry(self) -> PluginRegistryPort:
        if self._plugin_registry is None:
            self._plugin_registry = PluginRegistry(plugin_dir=self.config.plugin_dir)
            self._plugin_registry.discover()
        return self._plugin_registry

    @property
    def search_engine(self) -> SearchEnginePort:
        if self._search_engine is None:
            self._search_engine = HttpxScrapySearchEngine(
                http_client=self.http_client,
                cache=self.state.cache,
            )
        return self._search_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan manager - initialisiert alle Dependencies."""
    state = cast(AppState, app.state)

    # Cache initialisieren (wie in deinem main.py)
    state.cache = Cache("./cache")

    log.debug("Environment variable set", environment=state.config.environment)
    if state.config.environment == "dev":
        state.cache.clear()

    # Dependency Container erstellen
    container = DependencyContainer(state.config, state)

    # State befüllen
    state.http_client = container.http_client
    state.search_engine = container.search_engine
    state.plugins = container.plugin_registry

    log.info("dependencies_initialized")

    try:
        yield
    finally:
        await state.http_client.aclose()
        log.info("app_shutdown")
