"""Stremio stream resolution use case.

IMDb ID -> TMDB title -> parallel plugin search
-> convert -> sort -> StremioStream list.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Protocol
from uuid import uuid4

import structlog
from guessit import guessit

from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    RankedStream,
    ResolvedStream,
    StremioStream,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
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
    scoring_enabled: bool
    max_plugins_scored: int
    exploration_probability: float
    stremio_deadline_ms: int
    max_items_total: int
    max_items_per_plugin: int


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


# Transliteration table for characters that NFKD does not decompose.
# Covers German, Scandinavian, Polish, and other common Latin-script specials.
_TRANSLITERATION = str.maketrans(
    {
        "ß": "ss",
        "æ": "ae",
        "Æ": "Ae",
        "œ": "oe",
        "Œ": "Oe",
        "ø": "o",
        "Ø": "O",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
    }
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

    Applies fuzzy transliteration so titles from TMDB/Wikidata (which
    may contain Unicode diacritics, ligatures, or special characters)
    produce clean search queries that work on German streaming sites:

    1. Explicit transliteration (ß→ss, æ→ae, ø→o, ł→l, …)
    2. NFKD decomposition + combining-mark stripping (ū→u, é→e, ü→u)
    3. Punctuation removal (colons, semicolons, etc.)
    4. Whitespace normalization
    """
    # 1) Transliterate characters that NFKD cannot decompose
    text = title.translate(_TRANSLITERATION)
    # 2) NFKD decomposes: ū → u + combining macron, é → e + combining acute
    decomposed = unicodedata.normalize("NFKD", text)
    # Strip combining marks (category "M") to get plain base characters
    ascii_ish = "".join(c for c in decomposed if unicodedata.category(c)[0] != "M")
    # 3) Remove punctuation that breaks site searches (keep hyphens and apostrophes)
    cleaned = re.sub(r"[^\w\s\-']", " ", ascii_ish)
    # 4) Collapse whitespace
    return " ".join(cleaned.split())


# Callback type for probing hoster URLs at /stream time.
# Accepts list of (index, url) tuples, returns set of alive indices.
ProbeCallback = Callable[[list[tuple[int, str]]], Awaitable[set[int]]]

# Callback type for resolving hoster embed URLs to playable video URLs.
# Accepts (url, hoster_hint), returns ResolvedStream or None.
ResolveCallback = Callable[[str, str], Awaitable[ResolvedStream | None]]


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
        self._metrics = metrics
        self._score_store = score_store
        self._scoring_enabled = config.scoring_enabled
        self._max_plugins_scored = config.max_plugins_scored
        self._exploration_probability = config.exploration_probability
        self._stremio_deadline_ms = config.stremio_deadline_ms
        self._max_items_total = config.max_items_total
        self._max_items_per_plugin = config.max_items_per_plugin

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
        title_info = await self._resolve_title_info(request)
        title = title_info.title if title_info else None
        if not title:
            log.warning("stremio_title_not_found", imdb_id=request.imdb_id)
            return []

        query = _build_search_query(title)
        category = 2000 if request.content_type == "movie" else 5000

        plugin_names = self._plugins.get_by_provides("stream")
        both_names = self._plugins.get_by_provides("both")
        all_names = sorted(set(plugin_names + both_names))

        if not all_names:
            log.warning("stremio_no_stream_plugins")
            return []

        # Scored plugin selection (when enabled and scores are available)
        selected = await self._select_plugins(all_names, category)

        log.info(
            "stremio_search_start",
            imdb_id=request.imdb_id,
            title=title,
            query=query,
            plugin_count=len(selected),
            scored=len(selected) < len(all_names),
        )

        all_results = await self._search_plugins(
            selected,
            query,
            category,
            season=request.season,
            episode=request.episode,
        )

        if not all_results:
            log.info(
                "stremio_search_no_results",
                imdb_id=request.imdb_id,
                query=query,
            )
            return []

        # --- title-match filtering (CPU-bound guessit — offload to thread) ---
        loop = asyncio.get_running_loop()
        filtered = await loop.run_in_executor(
            None,
            lambda: self._filter_fn(
                all_results,
                title_info,
                self._title_match_threshold,
                year_bonus=self._title_year_bonus,
                year_penalty=self._title_year_penalty,
                sequel_penalty=self._title_sequel_penalty,
                year_tolerance_movie=self._title_year_tolerance_movie,
                year_tolerance_series=self._title_year_tolerance_series,
            ),
        )

        if not filtered:
            log.info(
                "stremio_all_filtered",
                imdb_id=request.imdb_id,
                query=query,
                total=len(all_results),
            )
            return []

        plugin_languages: dict[str, str] = {}
        for name in selected:
            try:
                plugin = self._plugins.get(name)
                lang = getattr(plugin, "default_language", None)
                if isinstance(lang, str):
                    plugin_languages[name] = lang
            except Exception:  # noqa: BLE001
                log.debug(
                    "stremio_plugin_language_lookup_failed",
                    plugin=name,
                    exc_info=True,
                )

        ranked = self._convert_fn(filtered, plugin_languages=plugin_languages)
        sorted_streams = self._sorter.sort(ranked)

        # Keep only the best-ranked stream per hoster (already sorted best-first)
        sorted_streams = _deduplicate_by_hoster(sorted_streams)

        streams = [
            _format_stream(
                s,
                reference_title=title_info.title if title_info else "",
                year=title_info.year if title_info else None,
                season=request.season,
                episode=request.episode,
            )
            for s in sorted_streams
        ]

        # Cache hoster URLs and replace with proxy play links
        if self._stream_link_repo and base_url:
            streams = await self._cache_and_proxy(streams, sorted_streams, base_url)

        log.info(
            "stremio_search_complete",
            imdb_id=request.imdb_id,
            query=query,
            result_count=len(all_results),
            filtered_count=len(filtered),
            stream_count=len(streams),
        )

        return streams

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
        if self._probe_fn and self._probe_at_stream_time:
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
        for i, (stream, sid) in enumerate(zip(streams, stream_ids)):
            resolved = resolved_map.get(i)
            if resolved is not None:
                # Direct video URL with proxyHeaders for Stremio's streaming server
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
                # Fallback: proxy through /play/ endpoint
                proxy_url = f"{base_url}/api/v1/stremio/play/{sid}"
                proxied.append(
                    StremioStream(
                        name=stream.name,
                        description=stream.description,
                        url=proxy_url,
                    )
                )
        return proxied

    async def _resolve_top_streams(
        self,
        ranked: list[RankedStream],
    ) -> dict[int, ResolvedStream]:
        """Resolve the top streams to direct video URLs in parallel.

        Returns a mapping of stream index -> ResolvedStream for
        successfully resolved streams.  Failed resolutions are omitted
        (those streams fall back to the /play/ proxy).
        """
        limit = min(len(ranked), self._max_probe_count)
        semaphore = asyncio.Semaphore(10)

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

        tasks = [_resolve_one(i) for i in range(limit)]
        results = await asyncio.gather(*tasks)

        resolved_count = 0
        resolved_map: dict[int, ResolvedStream] = {}
        for idx, resolved in results:
            if resolved is not None:
                resolved_map[idx] = resolved
                resolved_count += 1

        log.info(
            "stremio_resolve_complete",
            total=limit,
            resolved=resolved_count,
            failed=limit - resolved_count,
        )
        return resolved_map

    async def _resolve_title_info(
        self,
        request: StremioStreamRequest,
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
        info = await self._tmdb.get_title_and_year(request.imdb_id)
        if info is not None:
            return replace(info, content_type=request.content_type)
        return None

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

    async def _search_plugins(
        self,
        plugin_names: list[str],
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search all plugins in parallel with bounded concurrency."""
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def _search_one(name: str) -> list[SearchResult]:
            async with semaphore:
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
                    return []

        tasks = [_search_one(name) for name in plugin_names]
        results_per_plugin = await asyncio.gather(*tasks)

        all_results: list[SearchResult] = []
        for results in results_per_plugin:
            all_results.extend(results)
        return all_results

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
                    raw = await plugin.search(
                        query, category=category, season=season, episode=episode
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
