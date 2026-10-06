"""Factories shared by the tests of the Stremio stream use case and its phases.

The phase tests (title resolution, plugin selection, search, resolution,
answer) drive the whole use case through ``execute()``: they moved out of
``test_stremio_stream.py`` with the phases' code and kept their assertions.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
from scavengarr.domain.entities.stremio import ResolvedStream, StremioStreamRequest
from scavengarr.domain.plugins.base import SearchResult
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
        pool=ConcurrencyPool(httpx_slots=100, pw_slots=100),
        cache=cache,
        search_ttl_seconds=search_ttl_seconds,
        telemetry=telemetry,
        mirror_groups=mirror_groups,
        score_store=score_store,
    )
