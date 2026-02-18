"""Stremio stream resolution use case.

IMDb ID -> TMDB title -> parallel plugin search
-> convert -> sort -> StremioStream list.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Protocol
from uuid import uuid4

import structlog
from guessit import guessit
from unidecode import unidecode as _unidecode

from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    RankedStream,
    ResolvedStream,
    StremioStream,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.concurrency import (
    ConcurrencyBudgetPort,
    ConcurrencyPoolPort,
)
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.plugin_score_store import PluginScoreStorePort
from scavengarr.domain.ports.search_engine import SearchEnginePort
from scavengarr.domain.ports.stream_link_repository import StreamLinkRepository
from scavengarr.domain.ports.tmdb import TmdbClientPort

# ---------------------------------------------------------------------------
# Protocols — define what this use case needs from its dependencies.
# Infrastructure components satisfy these via structural subtyping.
# ---------------------------------------------------------------------------


class _StremioConfig(Protocol):
    """Configuration values consumed by StremioStreamUseCase."""

    max_concurrent_plugins: int
    max_concurrent_playwright: int
    plugin_timeout_seconds: float
    title_match_threshold: float
    title_year_bonus: float
    title_year_penalty: float
    title_sequel_penalty: float
    title_year_tolerance_movie: int
    title_year_tolerance_series: int
    max_results_per_plugin: int
    probe_at_stream_time: bool
    max_probe_count: int
    resolve_target_count: int
    scoring_enabled: bool
    max_plugins_scored: int
    exploration_probability: float


class _StreamSorter(Protocol):
    """Sorts RankedStreams by language, quality, and hoster scores."""

    def sort(self, streams: list[RankedStream]) -> list[RankedStream]: ...


class _MetricsRecorder(Protocol):
    """Records search and probe metrics."""

    def record_plugin_search(
        self,
        name: str,
        duration_ns: int,
        result_count: int,
        *,
        success: bool,
    ) -> None: ...

    def record_probe(
        self,
        total: int,
        alive: int,
        dead: int,
        cf_blocked: int,
        duration_ns: int,
    ) -> None: ...


class _CircuitBreaker(Protocol):
    """Per-plugin circuit breaker (skip after N consecutive failures)."""

    def allow(self, name: str) -> bool: ...
    def record_success(self, name: str) -> None: ...
    def record_failure(self, name: str) -> None: ...


# Type aliases for injected pure functions.
_ConvertFn = Callable[..., list[RankedStream]]
_TitleFilterFn = Callable[..., list[SearchResult]]

log = structlog.get_logger(__name__)


# Matches episode labels in download_links.
# Patterns: "1x5", "1x05", "2X10", "S01E05", "s1e5", "S02E10 Episode Title".
_EPISODE_LABEL_RE = re.compile(
    r"(?:^|\D)"
    r"(?:"
    r"(\d{1,2})\s*[xX]\s*(\d{1,4})"  # 1x5, 2X10
    r"|"
    r"[Ss](\d{1,2})\s*[Ee](\d{1,4})"  # S01E05, s1e5
    r")"
    r"(?:\D|$)"
)


def _parse_episode_from_label(label: str) -> tuple[int | None, int | None]:
    """Extract (season, episode) from a download_link label.

    Recognises patterns like ``1x5``, ``1x05``, ``2x10``,
    ``S01E05``, ``s1e5``.
    Returns ``(None, None)`` when no pattern is found.
    """
    m = _EPISODE_LABEL_RE.search(label)
    if m:
        # Groups 1,2 for NxM pattern; groups 3,4 for SxxExx pattern
        season = m.group(1) if m.group(1) is not None else m.group(3)
        episode = m.group(2) if m.group(2) is not None else m.group(4)
        return int(season), int(episode)
    return None, None


def _filter_links_by_episode(
    links: list[dict[str, str]],
    season: int | None,
    episode: int | None,
) -> list[dict[str, str]] | None:
    """Filter download_links by episode info in their labels.

    Returns:
        List of matching links when at least one link had episode info.
        ``None`` when no links contained parseable episode labels
        (meaning the filter cannot be applied).
    """
    matched: list[dict[str, str]] = []
    has_episode_info = False

    for link in links:
        label = link.get("label", "")
        l_season, l_episode = _parse_episode_from_label(label)

        if l_season is None and l_episode is None:
            # No episode info in this link — skip (orphaned mirror)
            continue

        has_episode_info = True

        if season is not None and l_season is not None and l_season != season:
            continue
        if episode is not None and l_episode is not None and l_episode != episode:
            continue

        matched.append(link)

    if not has_episode_info:
        return None

    return matched


def _filter_by_episode(
    results: list[SearchResult],
    season: int | None,
    episode: int | None,
) -> list[SearchResult]:
    """Filter results to match the requested season/episode.

    Uses guessit to parse release names. When the title has no parseable
    season/episode info, falls back to filtering individual download_links
    by their labels (e.g. ``1x5`` format from episode tabs).

    Results that cannot be parsed at all (no season/episode info in the
    title OR in download_links) are kept -- they might be different hosters
    for a single content page.
    """
    if season is None and episode is None:
        return results

    filtered: list[SearchResult] = []
    for r in results:
        info = guessit(r.title)
        r_season = info.get("season")
        r_episode = info.get("episode")

        # No parseable season/episode in title -> try download_links
        if r_season is None and r_episode is None:
            if r.download_links:
                kept = _filter_links_by_episode(r.download_links, season, episode)
                if kept is not None:
                    # Links had episode labels; only keep matching ones
                    if kept:
                        first_url = (
                            kept[0].get("link", "")
                            or kept[0].get("url", "")
                            or r.download_link
                        )
                        filtered.append(
                            replace(
                                r,
                                download_link=first_url,
                                download_links=kept,
                            )
                        )
                    # else: all links wrong episode -> drop entirely
                    continue

            # No download_links or no episode info in links -> keep
            filtered.append(r)
            continue

        # Season mismatch -> skip
        if season is not None and r_season is not None and r_season != season:
            continue

        # Episode mismatch -> skip
        if episode is not None and r_episode is not None and r_episode != episode:
            continue

        filtered.append(r)

    if len(filtered) < len(results):
        log.debug(
            "episode_filter_applied",
            season=season,
            episode=episode,
            before=len(results),
            after=len(filtered),
        )
    return filtered


def _format_stream(
    ranked: RankedStream,
    *,
    reference_title: str = "",
    year: int | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> StremioStream:
    """Convert a scored RankedStream into Stremio protocol format.

    When *reference_title* (from TMDB) is available it is always used as
    the stream name, enriched with year (movies) or season/episode (series).
    Falls back to release_name → ranked.title → source_plugin + quality.

    The description always starts with the source plugin name so that
    users can see which site the stream came from.
    """
    from scavengarr.domain.entities.stremio import StreamQuality

    quality_label = ranked.quality.name.replace("_", " ")
    show_quality = ranked.quality != StreamQuality.UNKNOWN

    # --- Build name (reference title has priority) ---
    title = reference_title or ranked.title
    if title:
        if season is not None and episode is not None:
            name = f"{title} S{season:02d}E{episode:02d}"
        elif year:
            name = f"{title} ({year})"
        else:
            name = title
        if show_quality:
            name = f"{name} {quality_label}"
    elif ranked.release_name:
        name = ranked.release_name
    else:
        name = (
            f"{ranked.source_plugin} {quality_label}"
            if ranked.source_plugin
            else quality_label
        )

    # --- Build description (source plugin always first) ---
    desc_parts: list[str] = []
    if ranked.source_plugin:
        desc_parts.append(ranked.source_plugin)
    if ranked.language:
        desc_parts.append(ranked.language.label)
    if ranked.hoster:
        desc_parts.append(ranked.hoster.upper())
    if ranked.size:
        desc_parts.append(ranked.size)

    return StremioStream(
        name=name,
        description=" | ".join(desc_parts) if desc_parts else "",
        url=ranked.url,
    )


def _deduplicate_by_hoster(streams: list[RankedStream]) -> list[RankedStream]:
    """Keep only the first (best-ranked) stream per hoster.

    The input must already be sorted by rank (best first).  For each
    hoster name, only the first occurrence is kept.  Streams with an
    empty hoster string are always kept (no dedup key).
    """
    seen: set[str] = set()
    result: list[RankedStream] = []
    for s in streams:
        if not s.hoster:
            result.append(s)
            continue
        if s.hoster not in seen:
            seen.add(s.hoster)
            result.append(s)
    return result


def _build_search_query(title: str) -> str:
    """Build a search query string from title.

    Returns the plain title without SxxExx suffix — season and episode
    are passed as separate parameters to each plugin so they can
    navigate directly to the correct content.

    Uses ``unidecode`` for universal Unicode→ASCII transliteration
    (130+ scripts), then strips punctuation and normalizes whitespace.
    """
    # 1) Universal Unicode → ASCII transliteration
    text = _unidecode(title)
    # 2) Remove punctuation that breaks site searches (keep hyphens and apostrophes)
    cleaned = re.sub(r"[^\w\s\-']", " ", text)
    # 3) Collapse whitespace
    return " ".join(cleaned.split())


def _build_search_queries(title: str) -> list[str]:
    """Build search query variants from a title.

    Returns a deduplicated list of queries in priority order:

    1. Full title (e.g. ``"Dune Part One"``)
    2. Base title before the first colon, if any (e.g. ``"Dune"``)

    Many German streaming sites list subtitled movies without the
    subtitle (``"Dune"`` instead of ``"Dune: Part One"``), so
    searching with only the full title misses results.  The title
    matcher filters out false positives from the shorter query.
    """
    full_query = _build_search_query(title)
    queries = [full_query]

    if ":" in title:
        base = title.split(":", maxsplit=1)[0].strip()
        if base:
            base_query = _build_search_query(base)
            if base_query and base_query != full_query:
                queries.append(base_query)

    return queries


def _build_multi_lang_reference(
    title_infos: dict[str, TitleMatchInfo | None],
    languages: list[str],
) -> TitleMatchInfo | None:
    """Build a TitleMatchInfo combining titles from multiple languages.

    The first available language becomes the primary title; additional
    language titles are merged into ``alt_titles``.  This lets the title
    matcher accept results in any of the plugin's configured languages.
    """
    primary: TitleMatchInfo | None = None
    extra_titles: list[str] = []
    for lang in languages:
        info = title_infos.get(lang)
        if not info:
            continue
        if primary is None:
            primary = info
        else:
            if (
                info.title
                and info.title != primary.title
                and info.title not in extra_titles
            ):
                extra_titles.append(info.title)
            for alt in info.alt_titles:
                if alt and alt != primary.title and alt not in extra_titles:
                    extra_titles.append(alt)
    if primary is None:
        return None
    # Merge, filtering out any that already appear in primary.alt_titles.
    existing = set(primary.alt_titles)
    merged = list(primary.alt_titles) + [t for t in extra_titles if t not in existing]
    return replace(primary, alt_titles=merged)


def _first_available_title(
    title_infos: dict[str, TitleMatchInfo | None],
    languages: list[str],
) -> TitleMatchInfo | None:
    """Return the first non-None TitleMatchInfo from the language list."""
    for lang in languages:
        info = title_infos.get(lang)
        if info is not None:
            return info
    return None


def _build_lang_group_queries(
    title_infos: dict[str, TitleMatchInfo | None],
    languages: list[str],
) -> list[str]:
    """Build deduplicated search queries for a group of languages."""
    queries: list[str] = []
    for lang in languages:
        info = title_infos.get(lang)
        if not info:
            continue
        for q in _build_search_queries(info.title):
            if q not in queries:
                queries.append(q)
        for alt in info.alt_titles:
            for q in _build_search_queries(alt):
                if q not in queries:
                    queries.append(q)
    return queries


# Video file extensions that Stremio can play directly.
_VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".m3u8",
        ".ts",
        ".webm",
        ".avi",
        ".flv",
        ".mov",
    }
)

# URL path fragments that indicate a direct video/HLS resource.
_VIDEO_PATH_HINTS = ("master.m3u8", "index.m3u8", "/hls/", "/get_video")


def _is_direct_video_url(resolved: ResolvedStream, original_url: str) -> bool:
    """Check whether a resolved stream points to an actual video resource.

    Returns ``False`` when the resolver merely validated availability and
    echoed back the original embed/download page URL (which Stremio cannot
    play).  Returns ``True`` when the resolver extracted a genuine video
    URL (``is_hls``, video extension, or a different URL with playback
    headers).
    """
    if resolved.is_hls:
        return True

    video_url_lower = resolved.video_url.lower()

    # Check for video file extensions
    for ext in _VIDEO_EXTENSIONS:
        if ext in video_url_lower:
            return True

    # Check for known video path patterns (CDN paths, HLS paths)
    for hint in _VIDEO_PATH_HINTS:
        if hint in video_url_lower:
            return True

    # If the resolver returned a *different* URL AND set custom headers
    # (e.g. Referer), it likely performed actual extraction.
    if resolved.video_url != original_url and resolved.headers:
        return True

    return False


# Callback type for probing hoster URLs at /stream time.
# Accepts list of (index, url) tuples, returns set of alive indices.
ProbeCallback = Callable[[list[tuple[int, str]]], Awaitable[set[int]]]

# Callback type for resolving hoster embed URLs to playable video URLs.
# Accepts (url, hoster_hint), returns ResolvedStream or None.
ResolveCallback = Callable[[str, str], Awaitable[ResolvedStream | None]]

# Callback type for warming up a shared Playwright browser.
# Returns (browser, playwright) tuple — opaque at this layer.
BrowserWarmupFn = Callable[[], Awaitable[tuple[Any, Any]]]


def _build_behavior_hints(
    resolved: ResolvedStream,
    *,
    user_agent: str,
) -> dict[str, Any]:
    """Build Stremio ``behaviorHints`` from a resolved stream.

    Sets ``notWebReady: true`` so Stremio routes the stream through its
    local streaming server, which applies the ``proxyHeaders`` to every
    request (including Range requests for seeking).

    Headers always include a browser User-Agent. If the resolver provided
    additional headers (e.g. Referer), they are merged in.
    """
    request_headers: dict[str, str] = {"User-Agent": user_agent}
    if resolved.headers:
        request_headers.update(resolved.headers)

    return {
        "notWebReady": True,
        "proxyHeaders": {
            "request": request_headers,
        },
    }


class StremioStreamUseCase:
    """Resolve Stremio stream requests into sorted stream links.

    Flow:
        1. Resolve IMDb ID to German title via TMDB.
        2. Discover plugins that provide streams.
        3. Search all plugins in parallel (bounded concurrency).
        4. Convert SearchResults to RankedStreams.
        5. Sort by language, quality, and hoster.
        6. Probe hoster URLs to filter dead links (optional).
        7. Format into StremioStream objects.
    """

    def __init__(
        self,
        *,
        tmdb: TmdbClientPort,
        plugins: PluginRegistryPort,
        search_engine: SearchEnginePort,
        config: _StremioConfig,
        sorter: _StreamSorter,
        convert_fn: _ConvertFn,
        filter_fn: _TitleFilterFn,
        user_agent: str,
        max_results_var: ContextVar[int | None],
        stream_link_repo: StreamLinkRepository | None = None,
        probe_fn: ProbeCallback | None = None,
        resolve_fn: ResolveCallback | None = None,
        metrics: _MetricsRecorder | None = None,
        score_store: PluginScoreStorePort | None = None,
        browser_warmup_fn: BrowserWarmupFn | None = None,
        pool: ConcurrencyPoolPort,
        circuit_breaker: _CircuitBreaker | None = None,
    ) -> None:
        self._tmdb = tmdb
        self._plugins = plugins
        self._search_engine = search_engine
        self._sorter = sorter
        self._convert_fn = convert_fn
        self._filter_fn = filter_fn
        self._user_agent = user_agent
        self._max_results_var = max_results_var
        self._max_concurrent = config.max_concurrent_plugins
        self._max_concurrent_pw = config.max_concurrent_playwright
        self._plugin_timeout = config.plugin_timeout_seconds
        self._title_match_threshold = config.title_match_threshold
        self._title_year_bonus = config.title_year_bonus
        self._title_year_penalty = config.title_year_penalty
        self._title_sequel_penalty = config.title_sequel_penalty
        self._title_year_tolerance_movie = config.title_year_tolerance_movie
        self._title_year_tolerance_series = config.title_year_tolerance_series
        self._max_results_per_plugin = config.max_results_per_plugin
        self._stream_link_repo = stream_link_repo
        self._probe_fn = probe_fn
        self._resolve_fn = resolve_fn
        self._probe_at_stream_time = config.probe_at_stream_time
        self._max_probe_count = config.max_probe_count
        self._probe_concurrency = config.probe_concurrency
        self._resolve_target = config.resolve_target_count
        self._metrics = metrics
        self._score_store = score_store
        self._scoring_enabled = config.scoring_enabled
        self._max_plugins_scored = config.max_plugins_scored
        self._exploration_probability = config.exploration_probability
        self._browser_warmup_fn = browser_warmup_fn
        self._pool = pool
        self._circuit_breaker = circuit_breaker

    async def execute(
        self,
        request: StremioStreamRequest,
        *,
        base_url: str = "",
    ) -> list[StremioStream]:
        """Resolve streams for a Stremio request.

        Args:
            request: Parsed stream request with IMDb ID and optional season/episode.
            base_url: Service base URL for generating proxy play links.

        Returns:
            Sorted list of StremioStream objects, best first.
            Empty list if title not found or no plugins match.
        """
        category = 2000 if request.content_type == "movie" else 5000

        plugin_names = self._plugins.get_by_provides("stream")
        both_names = self._plugins.get_by_provides("both")
        all_names = sorted(set(plugin_names + both_names))

        if not all_names:
            log.warning("stremio_no_stream_plugins")
            return []

        # Scored plugin selection (when enabled and scores are available)
        selected = await self._select_plugins(all_names, category)

        # --- Multi-language title resolution ---
        all_langs = self._collect_languages(selected)
        title_infos = await self._resolve_title_infos(request, sorted(all_langs))

        primary_title_info = _first_available_title(title_infos, sorted(all_langs))
        if primary_title_info is None:
            log.warning("stremio_title_not_found", imdb_id=request.imdb_id)
            return []

        # --- Per-language-group search + filter ---
        lang_groups = self._group_by_languages(selected)

        async with self._pool.request() as budget:
            all_results, filtered = await self._search_lang_groups(
                lang_groups,
                title_infos,
                request,
                category,
                all_names_count=len(all_names),
                selected_count=len(selected),
                budget=budget,
            )

        if not filtered:
            if all_results:
                log.info(
                    "stremio_all_filtered",
                    imdb_id=request.imdb_id,
                    total=len(all_results),
                )
            else:
                log.info(
                    "stremio_search_no_results",
                    imdb_id=request.imdb_id,
                )
            return []

        plugin_languages: dict[str, str] = {
            name: self._plugins.get_languages(name)[0]
            for name in selected
            if self._plugins.get_languages(name)
        }

        ranked = self._convert_fn(filtered, plugin_languages=plugin_languages)
        sorted_streams = self._sorter.sort(ranked)
        sorted_streams = _deduplicate_by_hoster(sorted_streams)

        streams = [
            _format_stream(
                s,
                reference_title=primary_title_info.title,
                year=primary_title_info.year,
                season=request.season,
                episode=request.episode,
            )
            for s in sorted_streams
        ]

        if self._stream_link_repo and base_url:
            streams = await self._cache_and_proxy(streams, sorted_streams, base_url)

        log.info(
            "stremio_search_complete",
            imdb_id=request.imdb_id,
            result_count=len(all_results),
            filtered_count=len(filtered),
            stream_count=len(streams),
        )

        return streams

    def _collect_languages(self, plugin_names: list[str]) -> set[str]:
        """Collect all unique languages across the given plugins."""
        all_langs: set[str] = set()
        for name in plugin_names:
            all_langs.update(self._plugins.get_languages(name))
        return all_langs

    def _group_by_languages(
        self, plugin_names: list[str]
    ) -> dict[tuple[str, ...], list[str]]:
        """Group plugin names by their identical language lists."""
        groups: dict[tuple[str, ...], list[str]] = {}
        for name in plugin_names:
            key = tuple(self._plugins.get_languages(name))
            groups.setdefault(key, []).append(name)
        return groups

    async def _search_lang_groups(
        self,
        lang_groups: dict[tuple[str, ...], list[str]],
        title_infos: dict[str, TitleMatchInfo | None],
        request: StremioStreamRequest,
        category: int,
        *,
        all_names_count: int,
        selected_count: int,
        budget: ConcurrencyBudgetPort,
    ) -> tuple[list[SearchResult], list[SearchResult]]:
        """Search and filter each language group, returning aggregated results.

        Language groups are searched in parallel so that e.g. German and
        English plugins start at the same time instead of sequentially.

        Returns (all_results, filtered_results).
        """

        async def _search_one_group(
            lang_key: tuple[str, ...],
            group_plugins: list[str],
        ) -> tuple[list[SearchResult], list[SearchResult]]:
            plugin_langs = list(lang_key)
            ref = _build_multi_lang_reference(title_infos, plugin_langs)
            if ref is None:
                return [], []

            queries = _build_lang_group_queries(title_infos, plugin_langs)
            if not queries:
                return [], []

            log.info(
                "stremio_search_start",
                imdb_id=request.imdb_id,
                title=ref.title,
                queries=queries,
                plugin_count=len(group_plugins),
                languages=plugin_langs,
                scored=selected_count < all_names_count,
            )

            group_results = await self._search_with_fallback(
                group_plugins,
                queries,
                category,
                season=request.season,
                episode=request.episode,
                budget=budget,
            )

            if not group_results:
                return group_results, []

            loop = asyncio.get_running_loop()
            group_filtered = await loop.run_in_executor(
                None,
                lambda ref=ref, gr=group_results: self._filter_fn(
                    gr,
                    ref,
                    self._title_match_threshold,
                    year_bonus=self._title_year_bonus,
                    year_penalty=self._title_year_penalty,
                    sequel_penalty=self._title_sequel_penalty,
                    year_tolerance_movie=self._title_year_tolerance_movie,
                    year_tolerance_series=self._title_year_tolerance_series,
                ),
            )
            return group_results, group_filtered

        group_tasks = [
            _search_one_group(lang_key, group_plugins)
            for lang_key, group_plugins in lang_groups.items()
        ]
        group_outcomes = await asyncio.gather(*group_tasks)

        all_results: list[SearchResult] = []
        filtered: list[SearchResult] = []
        for group_all, group_filt in group_outcomes:
            all_results.extend(group_all)
            filtered.extend(group_filt)

        return all_results, filtered

    async def _cache_and_proxy(
        self,
        streams: list[StremioStream],
        ranked: list[RankedStream],
        base_url: str,
    ) -> list[StremioStream]:
        """Cache hoster URLs and replace stream URLs with proxy play links.

        When a probe callback is configured and enabled, performs a
        lightweight GET probe on each hoster embed URL to filter dead
        links before caching. Only the top ``max_probe_count`` streams
        are probed; the rest pass through unchecked.

        When a resolve callback is configured, resolves hoster embed URLs
        to direct video URLs and attaches ``behaviorHints.proxyHeaders``
        so Stremio sends the correct HTTP headers (Referer, User-Agent)
        when playing the stream.  Streams that fail to resolve fall back
        to the ``/play/`` proxy endpoint.
        """
        # --- Probe step: filter dead links ---
        # Skip probing when a resolve callback is configured because
        # resolution implicitly checks liveness (failed → skipped).
        if self._probe_fn and self._probe_at_stream_time and not self._resolve_fn:
            limit = min(len(ranked), self._max_probe_count)
            probe_targets = [(i, ranked[i].url) for i in range(limit)]
            t0_probe = time.perf_counter_ns()
            alive_indices = await self._probe_fn(probe_targets)
            probe_duration = time.perf_counter_ns() - t0_probe

            dead = limit - len(alive_indices)
            log.info(
                "stremio_probe_complete",
                total=limit,
                alive=len(alive_indices),
                filtered=dead,
            )
            if self._metrics is not None:
                self._metrics.record_probe(
                    total=limit,
                    alive=len(alive_indices),
                    dead=dead,
                    cf_blocked=0,
                    duration_ns=probe_duration,
                )

            # Keep only alive streams (preserve order); unprobed streams pass through
            streams = [
                s for i, s in enumerate(streams) if i in alive_indices or i >= limit
            ]
            ranked = [
                r for i, r in enumerate(ranked) if i in alive_indices or i >= limit
            ]

        # --- Resolve step: extract direct video URLs + headers ---
        resolved_map: dict[int, ResolvedStream] = {}
        if self._resolve_fn:
            resolved_map = await self._resolve_top_streams(ranked)

        # --- Cache step (parallel writes) ---
        stream_ids = [uuid4().hex for _ in streams]
        links = [
            CachedStreamLink(
                stream_id=sid,
                hoster_url=ranked_s.url,
                title=ranked_s.title,
                hoster=ranked_s.hoster,
            )
            for sid, ranked_s in zip(stream_ids, ranked)
        ]
        await asyncio.gather(*(self._stream_link_repo.save(lnk) for lnk in links))

        proxied: list[StremioStream] = []
        skipped_echo = 0
        skipped_unresolved = 0
        has_resolver = bool(self._resolve_fn)
        for i, (stream, sid) in enumerate(zip(streams, stream_ids)):
            resolved = resolved_map.get(i)
            if resolved is not None:
                original_url = ranked[i].url if i < len(ranked) else ""
                if _is_direct_video_url(resolved, original_url):
                    # Resolver extracted a genuine video URL
                    hints = _build_behavior_hints(resolved, user_agent=self._user_agent)
                    proxied.append(
                        StremioStream(
                            name=stream.name,
                            description=stream.description,
                            url=resolved.video_url,
                            behavior_hints=hints,
                        )
                    )
                else:
                    # Resolver only validated availability but returned the
                    # embed/download page URL — Stremio cannot play HTML pages.
                    skipped_echo += 1
            elif has_resolver:
                # Resolver is configured but returned None — skip this stream.
                # The /play/ proxy would also fail (502).
                skipped_unresolved += 1
            else:
                # No resolver configured — proxy through /play/ endpoint
                proxy_url = f"{base_url}/api/v1/stremio/play/{sid}"
                proxied.append(
                    StremioStream(
                        name=stream.name,
                        description=stream.description,
                        url=proxy_url,
                    )
                )
        if skipped_echo or skipped_unresolved:
            log.info(
                "stremio_streams_skipped",
                skipped_echo=skipped_echo,
                skipped_unresolved=skipped_unresolved,
            )
        return proxied

    async def _resolve_top_streams(
        self,
        ranked: list[RankedStream],
    ) -> dict[int, ResolvedStream]:
        """Resolve the top streams to direct video URLs in parallel.

        Uses early-stop: once ``resolve_target_count`` genuine video URLs
        have been extracted, remaining tasks are cancelled.  This avoids
        waiting for slow hosters when enough playable streams are ready.

        Returns a mapping of stream index -> ResolvedStream for
        successfully resolved streams.  Failed resolutions are omitted
        (those streams fall back to the /play/ proxy).
        """
        limit = min(len(ranked), self._max_probe_count)
        semaphore = asyncio.Semaphore(self._probe_concurrency)

        async def _resolve_one(idx: int) -> tuple[int, ResolvedStream | None]:
            async with semaphore:
                r = ranked[idx]
                try:
                    return idx, await self._resolve_fn(r.url, r.hoster)
                except Exception:
                    log.debug(
                        "stremio_resolve_failed",
                        index=idx,
                        hoster=r.hoster,
                        url=r.url[:80],
                        exc_info=True,
                    )
                    return idx, None

        pending = {asyncio.create_task(_resolve_one(i)) for i in range(limit)}
        resolved_map: dict[int, ResolvedStream] = {}
        video_count = 0
        target = self._resolve_target
        attempted = 0

        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                attempted += 1
                idx, resolved = task.result()
                if resolved is not None:
                    resolved_map[idx] = resolved
                    original_url = ranked[idx].url if idx < len(ranked) else ""
                    if _is_direct_video_url(resolved, original_url):
                        video_count += 1

            if target > 0 and video_count >= target:
                break

        # Cancel remaining tasks once target reached
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        log.info(
            "stremio_resolve_complete",
            total=limit,
            attempted=attempted,
            resolved=len(resolved_map),
            video_streams=video_count,
            early_stop=target > 0 and video_count >= target,
        )
        return resolved_map

    async def _resolve_title_info(
        self,
        request: StremioStreamRequest,
        *,
        language: str = "de",
    ) -> TitleMatchInfo | None:
        """Resolve title + year from IMDb or TMDB ID for matching."""
        if request.imdb_id.startswith("tmdb:"):
            tmdb_id = request.imdb_id.removeprefix("tmdb:")
            title = await self._tmdb.get_title_by_tmdb_id(
                int(tmdb_id), request.content_type
            )
            if not title:
                return None
            return TitleMatchInfo(title=title, content_type=request.content_type)
        info = await self._tmdb.get_title_and_year(request.imdb_id, language=language)
        if info is not None:
            return replace(info, content_type=request.content_type)
        return None

    async def _resolve_title_infos(
        self,
        request: StremioStreamRequest,
        languages: list[str],
    ) -> dict[str, TitleMatchInfo | None]:
        """Fetch title info for each language in parallel.

        Returns a dict mapping language code to TitleMatchInfo (or None).
        For ``tmdb:`` prefixed IDs (no language variants), the same
        result is returned for every language.
        """
        infos = await asyncio.gather(
            *(self._resolve_title_info(request, language=lang) for lang in languages)
        )
        return dict(zip(languages, infos))

    async def _select_plugins(
        self,
        all_names: list[str],
        category: int,
    ) -> list[str]:
        """Select plugins to search, using scores when available.

        When scoring is disabled or no scores exist yet, returns all
        plugins (graceful cold-start fallback).

        When scoring is active, selects the top-N plugins by
        ``final_score`` and optionally adds one random exploration slot.
        """
        if not self._scoring_enabled or self._score_store is None:
            return all_names

        # Collect scores for each plugin (using "current" bucket as proxy)
        snapshots = await asyncio.gather(
            *(
                self._score_store.get_snapshot(name, category, "current")
                for name in all_names
            )
        )
        scored: list[tuple[str, float, float]] = [
            (name, snap.final_score, snap.confidence)
            if snap is not None
            else (name, 0.5, 0.0)
            for name, snap in zip(all_names, snapshots)
        ]

        # Cold-start guard: need at least 50% of plugins with confidence > 0.1
        confident_count = sum(1 for _, _, c in scored if c > 0.1)
        if confident_count < len(all_names) * 0.5:
            log.debug(
                "scored_selection_cold_start",
                confident=confident_count,
                total=len(all_names),
            )
            return all_names

        # Sort by final_score descending, pick top-N
        scored.sort(key=lambda x: x[1], reverse=True)
        top_n = scored[: self._max_plugins_scored]
        selected_names = [name for name, _, _ in top_n]

        # Exploration slot: with probability, add one mid-score plugin
        remaining = [
            (name, score, conf)
            for name, score, conf in scored[self._max_plugins_scored :]
            if conf >= 0.1
        ]
        if remaining and random.random() < self._exploration_probability:
            explorer = random.choice(remaining)
            selected_names.append(explorer[0])

        log.info(
            "scored_plugin_selection",
            top_n=[f"{n}:{s:.2f}" for n, s, _ in top_n],
            exploration=len(selected_names) > self._max_plugins_scored,
            total_available=len(all_names),
        )
        return selected_names

    async def _search_with_fallback(
        self,
        plugin_names: list[str],
        queries: list[str],
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
        budget: ConcurrencyBudgetPort,
    ) -> list[SearchResult]:
        """Search plugins with all query variants, deduplicate results.

        Concurrency is managed by the global *budget* (from
        ConcurrencyPool) which provides fair-share httpx + PW slots.

        When a browser warmup function is configured, a fire-and-forget
        warmup task starts the shared Chromium process in the background
        while httpx plugins search.  Playwright plugins obtain the
        shared browser from their injected pool reference (set at
        composition time).

        The first query's results are always kept in full.  Subsequent
        (fallback) queries only add results whose ``download_link`` was
        not already seen, to avoid duplicates from the same plugin
        matching on both the full title and the shorter base title.
        """
        # --- Fire-and-forget pre-warm for shared Playwright browser ---
        if self._browser_warmup_fn is not None:
            task = asyncio.create_task(
                self._browser_warmup_fn(),
                name="browser-warmup",
            )
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )

        search_tasks = [
            self._search_plugins(
                plugin_names,
                q,
                category,
                season=season,
                episode=episode,
                budget=budget,
            )
            for q in queries
        ]
        results_per_query = await asyncio.gather(*search_tasks)

        # First query's results are kept unconditionally.
        all_results: list[SearchResult] = list(results_per_query[0])
        if len(results_per_query) > 1:
            seen: set[str] = {r.download_link for r in all_results}
            for results in results_per_query[1:]:
                for r in results:
                    if r.download_link not in seen:
                        seen.add(r.download_link)
                        all_results.append(r)
        return all_results

    async def _search_plugins(
        self,
        plugin_names: list[str],
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
        budget: ConcurrencyBudgetPort,
    ) -> list[SearchResult]:
        """Search all plugins in parallel with bounded concurrency.

        Uses the global concurrency pool's fair-share budget to manage
        httpx and Playwright slot allocation across requests.
        """

        async def _search_one(name: str) -> list[SearchResult]:
            is_pw = self._plugins.get_mode(name) == "playwright"
            if is_pw:
                async with budget.acquire_pw():
                    return await self._run_plugin_with_timeout(
                        name, query, category, season=season, episode=episode
                    )
            else:
                async with budget.acquire_httpx():
                    return await self._run_plugin_with_timeout(
                        name, query, category, season=season, episode=episode
                    )

        tasks = [_search_one(name) for name in plugin_names]
        results_per_plugin = await asyncio.gather(*tasks)

        all_results: list[SearchResult] = []
        for results in results_per_plugin:
            all_results.extend(results)
        return all_results

    async def _run_plugin_with_timeout(
        self,
        name: str,
        query: str,
        category: int | None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Run a single plugin search with timeout, catching errors."""
        # Circuit breaker: skip plugins that have been failing consistently
        if self._circuit_breaker is not None and not self._circuit_breaker.allow(name):
            log.debug("stremio_plugin_circuit_open", plugin=name)
            return []

        try:
            return await asyncio.wait_for(
                self._search_single_plugin(
                    name, query, category, season=season, episode=episode
                ),
                timeout=self._plugin_timeout,
            )
        except TimeoutError:
            log.warning(
                "stremio_plugin_timeout",
                plugin=name,
                timeout=self._plugin_timeout,
            )
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure(name)
            return []

    @staticmethod
    async def _dispatch_search(
        plugin: object,
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Dispatch to isolated_search() when available, else search()."""
        if hasattr(plugin, "isolated_search") and callable(plugin.isolated_search):
            return await plugin.isolated_search(
                query, category, season=season, episode=episode
            )
        return await plugin.search(
            query, category=category, season=season, episode=episode
        )

    async def _search_single_plugin(
        self,
        name: str,
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search a single plugin, catching and logging errors.

        Python plugins (with search() but no scraping stages) are called
        directly, then their results are validated via SearchEngine.
        YAML plugins (with scraping stages) are delegated to the SearchEngine.
        """
        try:
            plugin = self._plugins.get(name)
        except Exception:
            log.warning("stremio_plugin_not_found", plugin=name, exc_info=True)
            return []

        t0 = time.perf_counter_ns()
        success = False
        cancelled = False
        results: list[SearchResult] = []
        try:
            if (
                hasattr(plugin, "search")
                and callable(plugin.search)
                and not hasattr(plugin, "scraping")
            ):
                # Python plugin: call directly, validate results
                # Set max_results context so plugins limit pagination
                token = self._max_results_var.set(self._max_results_per_plugin)
                try:
                    raw = await self._dispatch_search(
                        plugin, query, category, season=season, episode=episode
                    )
                finally:
                    self._max_results_var.reset(token)
                raw = _filter_by_episode(raw, season, episode)
                results = await self._search_engine.validate_results(raw)
            else:
                # YAML plugin: delegate to search engine
                results = await self._search_engine.search(
                    plugin, query, category=category
                )
            success = True
        except Exception:
            log.warning("stremio_plugin_search_error", plugin=name, exc_info=True)
            results = []
        except BaseException:
            cancelled = True
            log.warning("stremio_plugin_search_cancelled", plugin=name)
            raise
        finally:
            duration_ns = time.perf_counter_ns() - t0
            if self._metrics is not None:
                self._metrics.record_plugin_search(
                    name,
                    duration_ns,
                    len(results),
                    success=success,
                )
            # Record circuit breaker outcome — but NOT on cancellation
            # (BaseException), since the timeout handler in
            # _run_plugin_with_timeout records that case instead.
            if self._circuit_breaker is not None and not cancelled:
                if success:
                    self._circuit_breaker.record_success(name)
                else:
                    self._circuit_breaker.record_failure(name)

        # Tag results with source plugin for downstream use
        for r in results:
            if isinstance(r, SearchResult) and not r.metadata.get("source_plugin"):
                r.metadata["source_plugin"] = name

        log.debug(
            "stremio_plugin_search_done",
            plugin=name,
            result_count=len(results),
        )
        return results
