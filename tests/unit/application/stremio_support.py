"""Factories shared by the tests of the Stremio stream use case and its phases.

The phase tests (title resolution, plugin selection, search, resolution,
answer) drive the whole use case through ``execute()``: they moved out of
``test_stremio_stream.py`` with the phases' code and kept their assertions.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

from scavengarr.application.stremio.search_cache import CachedSearch
from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
from scavengarr.domain.entities.stremio import (
    ResolvedStream,
    StremioStream,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.anime_ids import NO_ANIME_IDS, AnimeIdResolverPort
from scavengarr.domain.ports.browser_fetcher import PageClaim, page_claim
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort
from scavengarr.infrastructure.concurrency import ConcurrencyPool
from scavengarr.infrastructure.config.schema import StremioConfig
from scavengarr.infrastructure.plugins.constants import (
    DEFAULT_USER_AGENT,
    search_max_results,
)
from scavengarr.infrastructure.stremio.episode_filter import filter_by_episode
from scavengarr.infrastructure.stremio.stream_converter import convert_search_results
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter
from scavengarr.infrastructure.stremio.title_matcher import filter_by_title_match


def make_config(**overrides: object) -> StremioConfig:
    defaults = {
        "max_concurrent_plugins": 5,
        "language_scores": {"de": 1000, "en": 150},
        "default_language_score": 100,
        "quality_multiplier": 10,
        "hoster_scores": {"voe": 4},
    }
    defaults.update(overrides)
    return StremioConfig(**defaults)


def make_request(
    *,
    imdb_id: str = "tt1234567",
    content_type: str = "movie",
    season: int | None = None,
    episode: int | None = None,
) -> StremioStreamRequest:
    return StremioStreamRequest(
        imdb_id=imdb_id,
        content_type=content_type,
        season=season,
        episode=episode,
    )


def make_search_result(
    *,
    title: str = "Test Movie",
    download_link: str = "https://voe.sx/e/abc",
    download_links: list[dict[str, str]] | None = None,
    release_name: str | None = None,
    metadata: dict | None = None,
) -> SearchResult:
    return SearchResult(
        title=title,
        download_link=download_link,
        download_links=download_links,
        release_name=release_name,
        metadata=metadata or {},
    )


def make_use_case(
    *,
    tmdb: AsyncMock | None = None,
    plugins: MagicMock | None = None,
    search_engine: AsyncMock | None = None,
    config: StremioConfig | None = None,
    stream_link_repo: AsyncMock | None = None,
    resolve_fn: Callable[..., Awaitable[ResolvedStream | None]] | None = None,
    cached_resolution_fn: Callable[[str], tuple[bool, ResolvedStream | None]]
    | None = None,
    cache: AsyncMock | None = None,
    search_ttl_seconds: int = 0,
    telemetry: TelemetryPort = NO_TELEMETRY,
    mirror_groups: dict[str, str] | None = None,
    score_store: AsyncMock | None = None,
    anime_ids: AnimeIdResolverPort = NO_ANIME_IDS,
    pool: ConcurrencyPool | None = None,
) -> StremioStreamUseCase:
    engine = search_engine or AsyncMock()
    # Default: validate_results returns input unchanged
    if not search_engine:
        engine.validate_results = AsyncMock(side_effect=lambda r: r)
        engine.search = AsyncMock(return_value=[])
    cfg = config or make_config()
    if plugins is None:
        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
    return StremioStreamUseCase(
        tmdb=tmdb or AsyncMock(),
        plugins=plugins,
        search_engine=engine,
        config=cfg,
        sorter=StreamSorter(cfg),
        convert_fn=convert_search_results,
        filter_fn=filter_by_title_match,
        episode_filter_fn=filter_by_episode,
        user_agent=DEFAULT_USER_AGENT,
        max_results_var=search_max_results,
        stream_link_repo=stream_link_repo,
        resolve_fn=resolve_fn,
        cached_resolution_fn=cached_resolution_fn,
        pool=pool or ConcurrencyPool(httpx_slots=100, pw_slots=100),
        cache=cache,
        search_ttl_seconds=search_ttl_seconds,
        telemetry=telemetry,
        mirror_groups=mirror_groups,
        score_store=score_store,
        anime_ids=anime_ids,
    )


def resolving_use_case(
    links: list[dict[str, str]],
    resolve: object,
    config: StremioConfig | None = None,
) -> StremioStreamUseCase:
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )
    srs = [
        make_search_result(
            title="Iron Man", release_name=link.pop("release"), download_links=[link]
        )
        for link in links
    ]
    mock_plugin = AsyncMock()
    mock_plugin.search = AsyncMock(return_value=srs)
    mock_plugin.isolated_search = mock_plugin.search
    engine = AsyncMock()
    engine.validate_results = AsyncMock(side_effect=lambda r: r)
    plugins = MagicMock()
    plugins.get_languages.return_value = ["de"]
    plugins.get_by_provides.side_effect = lambda p: ["hdfilme"] if p == "stream" else []
    plugins.get.return_value = mock_plugin
    return make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        search_engine=engine,
        config=config,
        stream_link_repo=AsyncMock(),
        resolve_fn=AsyncMock(side_effect=resolve),
    )


def _stream_id(url: str) -> str:
    path = urlparse(url).path
    if "/play/" in path:
        return path.rsplit("/play/", 1)[1]
    return path.split("/proxy/", 1)[1].split("/", 1)[0]


def video(uc: StremioStreamUseCase, stream: StremioStream) -> str:
    """The video URL behind *stream*: /play and the HLS proxy serve the
    stored link's."""
    repo = uc._stream_link_repo
    assert isinstance(repo, AsyncMock)
    saved = {c.args[0].stream_id: c.args[0] for c in repo.save.await_args_list}
    return saved[_stream_id(stream.url)].video_url


def resolved(url: str) -> ResolvedStream:
    return ResolvedStream(
        video_url=f"https://cdn.example/{url.rsplit('/', 1)[-1]}.mp4",
        headers={"Referer": "https://voe.sx/"},
    )


SEARCH_KEY = "stremio:search:movie:tt1234567:None:None"

SEARCH_TTL = 1800


def memory_cache() -> AsyncMock:
    """CachePort keeping its entries in ``cache.data``."""
    data: dict[str, object] = {}
    cache = AsyncMock()
    cache.data = data
    cache.get = AsyncMock(side_effect=lambda key: data.get(key))
    cache.set = AsyncMock(
        side_effect=lambda key, value, *, ttl=None: data.__setitem__(key, value)
    )
    return cache


def hit(link: str, title: str = "Iron Man") -> SearchResult:
    """A search result with one hoster link (one stream per hoster)."""
    return make_search_result(
        title=title,
        download_link=link,
        download_links=[{"url": link, "quality": "1080p"}],
    )


def fake_site(
    results: list[SearchResult],
    delay: float = 0.0,
    *,
    cancelled: asyncio.Event | None = None,
) -> AsyncMock:
    """Plugin answering with *results* after *delay* seconds."""

    async def _search(*_args: object, **_kwargs: object) -> list[SearchResult]:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            if cancelled is not None:
                cancelled.set()
            raise
        return results

    plugin = AsyncMock()
    plugin.isolated_search = AsyncMock(side_effect=_search)
    return plugin


def cached_use_case(
    sites: dict[str, AsyncMock],
    cache: AsyncMock,
    *,
    ttl: int = SEARCH_TTL,
    hard: float = 1.0,
    telemetry: TelemetryPort = NO_TELEMETRY,
    pool: ConcurrencyPool | None = None,
) -> StremioStreamUseCase:
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )
    plugins = MagicMock()
    plugins.get_languages.return_value = ["de"]
    plugins.get_by_provides.side_effect = lambda p: (
        sorted(sites) if p == "stream" else []
    )
    plugins.get.side_effect = sites.__getitem__
    return make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        config=make_config(
            plugin_timeout_seconds=hard,
            stream_deadline_seconds=hard + 1.0,
        ),
        cache=cache,
        search_ttl_seconds=ttl,
        telemetry=telemetry,
        pool=pool,
    )


async def eventually(check: Callable[[], bool], timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "condition not met in time"
        await asyncio.sleep(0.01)


def cached_links(cache: AsyncMock) -> list[str]:
    entry = cache.data.get(SEARCH_KEY)
    return sorted(r.download_link for r in entry.results) if entry else []


class Resolutions:
    """Resolver registry stand-in: resolve() caches, cached() peeks."""

    def __init__(
        self,
        *,
        alive: tuple[str, ...] = (),
        dead: tuple[str, ...] = (),
        delay: float = 0.0,
    ) -> None:
        self.store: dict[str, ResolvedStream | None] = {u: resolved(u) for u in alive}
        self.store.update(dict.fromkeys(dead))
        self.delay = delay
        self.calls: list[str] = []
        self.cancelled = asyncio.Event()
        self.running = 0
        self.most_at_once = 0

    async def resolve(self, url: str, hoster: str = "") -> ResolvedStream | None:
        if url in self.store:
            return self.store[url]
        self.calls.append(url)
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.running -= 1
        self.store[url] = resolved(url)
        return self.store[url]

    def cached(self, url: str) -> tuple[bool, ResolvedStream | None]:
        return url in self.store, self.store.get(url)


class ClaimSeeing(Resolutions):
    """Notes the page claim each link resolved under."""

    def __init__(self, *, alive: tuple[str, ...] = ()) -> None:
        super().__init__(alive=alive)
        self.claims: dict[str, PageClaim | None] = {}

    async def resolve(self, url: str, hoster: str = "") -> ResolvedStream | None:
        self.claims.setdefault(url, page_claim.get())
        return await super().resolve(url, hoster)


VOE = "https://voe.sx/e/best"

VOE_2 = "https://voe.sx/e/second"

DOOD = "https://dood.to/e/new"


def from_cache(
    links: list[dict[str, str]],
    resolutions: Resolutions,
    *,
    cached: bool = True,
    telemetry: TelemetryPort = NO_TELEMETRY,
) -> StremioStreamUseCase:
    """Use case whose search for the title is in the cache (or, with
    *cached* False, comes from a plugin)."""
    results = [
        make_search_result(
            title="Iron Man", release_name=link.pop("release"), download_links=[link]
        )
        for link in links
    ]
    cache = memory_cache()
    if cached:
        cache.data[SEARCH_KEY] = CachedSearch(
            results=results, total=len(results), stored_at=time.time()
        )
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )
    plugins = MagicMock()
    plugins.get_languages.return_value = ["de"]
    plugins.get_by_provides.side_effect = lambda p: ["a"] if p == "stream" else []
    plugins.get.return_value = fake_site(results)
    return make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        config=make_config(),
        stream_link_repo=AsyncMock(),
        resolve_fn=resolutions.resolve,
        cached_resolution_fn=resolutions.cached,
        cache=cache,
        search_ttl_seconds=SEARCH_TTL,
        telemetry=telemetry,
    )


def hoster_link(url: str, release: str = "Iron.Man.2008.German.1080p.BluRay") -> dict:
    return {"url": url, "hoster": url.split("/")[2].split(".")[0], "release": release}


def cached_titles(
    titles: dict[str, list[dict[str, str]]],
    resolutions: Resolutions,
    **config: object,
) -> StremioStreamUseCase:
    """Use case with a search-cache entry per title (IMDb id: its links)."""
    cache = memory_cache()
    for imdb_id, links in titles.items():
        results = [
            make_search_result(
                title="Iron Man",
                release_name=link.pop("release"),
                download_links=[link],
            )
            for link in links
        ]
        cache.data[f"stremio:search:movie:{imdb_id}:None:None"] = CachedSearch(
            results=results, total=len(results), stored_at=time.time()
        )
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )
    plugins = MagicMock()
    plugins.get_languages.return_value = ["de"]
    plugins.get_by_provides.side_effect = lambda p: ["a"] if p == "stream" else []
    return make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        config=make_config(**config),
        stream_link_repo=AsyncMock(),
        resolve_fn=resolutions.resolve,
        cached_resolution_fn=resolutions.cached,
        cache=cache,
        search_ttl_seconds=SEARCH_TTL,
    )


def answering_use_case(
    sites: dict[str, AsyncMock],
    cache: AsyncMock,
    resolutions: Resolutions,
    telemetry: TelemetryPort = NO_TELEMETRY,
    *,
    mirror_groups: dict[str, str] | None = None,
    score_store: AsyncMock | None = None,
    pool: ConcurrencyPool | None = None,
    **config: object,
) -> StremioStreamUseCase:
    """Use case that searches *sites* and resolves with *resolutions*."""
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )
    plugins = MagicMock()
    plugins.get_languages.return_value = ["de"]
    plugins.get_by_provides.side_effect = lambda p: (
        sorted(sites) if p == "stream" else []
    )
    plugins.get.side_effect = sites.__getitem__
    return make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        config=make_config(**config),
        stream_link_repo=AsyncMock(),
        resolve_fn=resolutions.resolve,
        cached_resolution_fn=resolutions.cached,
        cache=cache,
        search_ttl_seconds=SEARCH_TTL,
        telemetry=telemetry,
        mirror_groups=mirror_groups,
        score_store=score_store,
        pool=pool,
    )
