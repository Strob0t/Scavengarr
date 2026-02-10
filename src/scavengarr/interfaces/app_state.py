"""Application state container for FastAPI dependency injection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from starlette.datastructures import State

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.infrastructure.config import AppConfig

if TYPE_CHECKING:
    from scavengarr.domain.ports import (
        CachePort,
        CrawlJobRepository,
        PluginRegistryPort,
        SearchEnginePort,
    )


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

    # Application Services
    crawljob_factory: CrawlJobFactory
