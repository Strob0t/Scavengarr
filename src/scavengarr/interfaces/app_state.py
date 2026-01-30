from __future__ import annotations

import httpx
from diskcache import Cache
from starlette.datastructures import State

from scavengarr.domain.ports.search_engine import SearchEnginePort
from scavengarr.infrastructure.config import AppConfig
from scavengarr.infrastructure.plugins import PluginRegistry


class AppState(State):
    config: AppConfig
    plugins: PluginRegistry
    http_client: httpx.AsyncClient
    search_engine: SearchEnginePort
    cache: Cache
