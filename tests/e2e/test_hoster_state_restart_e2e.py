"""E2E: a resolution outlives a restart (openspec persist-resolver-state).

A stream request resolves a link; the app stops (its state store writes the
snapshot) and starts again on the same cache backend (the store restores
it). The next request for the title answers from the search cache with the
restored resolution, without resolving the link again.

Real: StremioStreamUseCase, HosterResolverRegistry, HosterStateStore,
PluginCircuitBreaker, StreamSorter, the converters and filters.
Mocked: TMDB, the plugin registry, the search engine, the link repository,
the hoster resolver, the cache backend (a dict that pickles).
"""

from __future__ import annotations

import asyncio
import pickle
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import structlog
from fastapi import FastAPI

from scavengarr.application.use_cases.stremio_links import StremioLinks
from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
from scavengarr.domain.entities.stremio import ResolvedStream, TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.concurrency import ConcurrencyPool
from scavengarr.infrastructure.config.schema import StremioConfig
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)
from scavengarr.infrastructure.hoster_resolvers.state_store import HosterStateStore
from scavengarr.infrastructure.plugins.constants import (
    DEFAULT_USER_AGENT,
    search_max_results,
)
from scavengarr.infrastructure.stremio.episode_filter import filter_by_episode
from scavengarr.infrastructure.stremio.stream_converter import convert_search_results
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter
from scavengarr.infrastructure.stremio.title_matcher import filter_by_title_match
from scavengarr.interfaces.api.stremio.router import router

_STREAM_URL = "/api/v1/stremio/stream/movie/tt0371746.json"
_LINK = "https://voe.sx/e/abc123"


class _Backend:
    """The cache backend both runs share: a dict that pickles like the
    diskcache and Redis adapters."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.search_stored = asyncio.Event()

    async def get(self, key: str) -> Any:
        return pickle.loads(self.data[key]) if key in self.data else None

    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        self.data[key] = pickle.dumps(value)
        if key.startswith("stremio:search:"):
            self.search_stored.set()

    async def delete(self, key: str) -> bool:
        return self.data.pop(key, None) is not None


class _Plugin:
    name = "hdfilme"
    provides = "stream"
    default_language = "de"

    async def search(self, *_args: Any, **_kwargs: Any) -> list[SearchResult]:
        return [
            SearchResult(
                title="Iron Man",
                download_link=_LINK,
                download_links=[
                    {
                        "hoster": "VOE",
                        "link": _LINK,
                        "language": "German Dub",
                        "quality": "1080p",
                    }
                ],
                category=2000,
                metadata={"source_plugin": "hdfilme"},
            )
        ]

    async def isolated_search(self, *args: Any, **kwargs: Any) -> list[SearchResult]:
        return await self.search(*args, **kwargs)


class _Run:
    """One run of the app on *backend*: the registry with a VOE resolver
    that resolves to *video_url*, its state store and the Stremio router."""

    def __init__(self, backend: _Backend, video_url: str) -> None:
        self.resolver = MagicMock()
        self.resolver.name = "voe"
        self.resolver.resolve = AsyncMock(
            return_value=ResolvedStream(
                video_url=video_url, headers={"Referer": "https://voe.sx/"}
            )
        )
        self.registry = HosterResolverRegistry(resolvers=[self.resolver])
        self.store = HosterStateStore(
            backend,
            self.registry,
            {"plugin": PluginCircuitBreaker(), "hoster": PluginCircuitBreaker()},
        )
        self.links = AsyncMock()
        self.app = self._app(backend)

    def _app(self, backend: _Backend) -> FastAPI:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )
        plugin = _Plugin()
        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = plugin
        plugins.get_languages.return_value = ["de"]
        plugins.get_mode.return_value = "httpx"
        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)
        config = StremioConfig()
        use_case = StremioStreamUseCase(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            config=config,
            sorter=StreamSorter(config),
            convert_fn=convert_search_results,
            filter_fn=filter_by_title_match,
            episode_filter_fn=filter_by_episode,
            user_agent=DEFAULT_USER_AGENT,
            max_results_var=search_max_results,
            stream_link_repo=self.links,
            resolve_fn=self.registry.resolve,
            cached_resolution_fn=self.registry.cached,
            pool=ConcurrencyPool(),
            cache=backend,
            search_ttl_seconds=3600,
        )
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app_config = MagicMock()
        app_config.environment = "dev"
        app.state.config = app_config
        app.state.plugins = plugins
        app.state.stremio_catalog_uc = None
        app.state.stremio_stream_uc = use_case
        app.state.stream_link_repo = self.links
        app.state.hoster_resolver_registry = self.registry
        app.state.stremio_links = StremioLinks(repo=self.links, resolver=self.registry)
        return app

    async def request_streams(self) -> list[dict[str, Any]]:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:
            response = await client.get(_STREAM_URL)
        assert response.status_code == 200
        return response.json()["streams"]

    def saved_video_urls(self) -> list[str]:
        return [c.args[0].video_url for c in self.links.save.await_args_list]


async def test_the_answer_after_a_restart_uses_the_restored_resolution() -> None:
    backend = _Backend()
    before = _Run(backend, "https://cdn.voe.sx/delivery/before.mp4")
    await before.store.restore()
    assert await before.request_streams()
    await asyncio.wait_for(backend.search_stored.wait(), timeout=5)
    await before.store.aclose()

    after = _Run(backend, "https://cdn.voe.sx/delivery/after.mp4")
    await after.store.restore()
    with structlog.testing.capture_logs() as logs:
        streams = await after.request_streams()

    assert len(streams) == 1
    assert after.saved_video_urls() == ["https://cdn.voe.sx/delivery/before.mp4"]
    after.resolver.resolve.assert_not_awaited()
    assert any(e["event"] == "stremio_resolve_from_cache" for e in logs)
