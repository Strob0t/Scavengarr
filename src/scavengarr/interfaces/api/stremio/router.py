"""Stremio addon API endpoints (manifest, catalog, stream)."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from scavengarr.domain.entities.stremio import (
    StremioContentType,
    StremioStream,
    StremioStreamRequest,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.stremio.stream_converter import convert_search_results
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter
from scavengarr.interfaces.app_state import AppState

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/stremio", tags=["stremio"])

_ADDON_ID = "community.scavengarr"
_ADDON_VERSION = "0.1.0"


def _build_manifest(plugin_names: list[str]) -> dict[str, Any]:
    """Build the Stremio addon manifest."""
    return {
        "id": _ADDON_ID,
        "version": _ADDON_VERSION,
        "name": "Scavengarr",
        "description": "German streaming links from multiple sources",
        "types": ["movie", "series"],
        "catalogs": [
            {
                "type": "movie",
                "id": "scavengarr-trending-movies",
                "name": "Scavengarr Trending Movies",
                "extra": [{"name": "search", "isRequired": False}],
            },
            {
                "type": "series",
                "id": "scavengarr-trending-series",
                "name": "Scavengarr Trending Series",
                "extra": [{"name": "search", "isRequired": False}],
            },
        ],
        "resources": ["catalog", "stream"],
        "idPrefixes": ["tt", "tmdb:"],
        "behaviorHints": {
            "adult": False,
            "configurable": False,
        },
    }


def _parse_stream_id(content_type: str, raw_id: str) -> StremioStreamRequest | None:
    """Parse Stremio stream ID into a StremioStreamRequest.

    Movies: "tt1234567" or "tmdb:12345"
    Series: "tt1234567:1:5" or "tmdb:12345:1:5" (season 1, episode 5)
    """
    if content_type not in ("movie", "series"):
        return None

    ct: StremioContentType = cast(StremioContentType, content_type)

    # Handle tmdb:{id} format (from our own catalog)
    if raw_id.startswith("tmdb:"):
        parts = raw_id.split(":")
        tmdb_part = f"tmdb:{parts[1]}"  # "tmdb:12345"

        if ct == "series" and len(parts) == 4:
            try:
                season = int(parts[2])
                episode = int(parts[3])
            except ValueError:
                return None
            return StremioStreamRequest(
                imdb_id=tmdb_part,
                content_type=ct,
                season=season,
                episode=episode,
            )
        return StremioStreamRequest(imdb_id=tmdb_part, content_type=ct)

    # Handle tt* format (real IMDb IDs)
    if not raw_id.startswith("tt"):
        return None

    parts = raw_id.split(":")
    imdb_id = parts[0]

    if ct == "series" and len(parts) == 3:
        try:
            season = int(parts[1])
            episode = int(parts[2])
        except ValueError:
            return None
        return StremioStreamRequest(
            imdb_id=imdb_id,
            content_type=ct,
            season=season,
            episode=episode,
        )

    return StremioStreamRequest(imdb_id=imdb_id, content_type=ct)


def _format_stremio_stream(stream: StremioStream) -> dict[str, str]:
    """Convert a StremioStream dataclass to Stremio JSON format."""
    return {
        "name": stream.name,
        "description": stream.description,
        "url": stream.url,
    }


async def _search_plugin(
    plugin: Any,
    query: str,
    engine: Any,
    category: int | None,
) -> list[SearchResult]:
    """Execute search on a single plugin, returning results or empty on error."""
    try:
        if (
            hasattr(plugin, "search")
            and callable(plugin.search)
            and not hasattr(plugin, "scraping")
        ):
            raw = await plugin.search(query, category=category)
            return await engine.validate_results(raw)
        else:
            return await engine.search(plugin, query, category=category)
    except Exception:
        log.warning(
            "stremio_plugin_search_failed",
            plugin=getattr(plugin, "name", "unknown"),
            query=query,
            exc_info=True,
        )
        return []


@router.get("/manifest.json")
async def stremio_manifest(request: Request) -> JSONResponse:
    """Serve the Stremio addon manifest."""
    state = cast(AppState, request.app.state)
    plugin_names = state.plugins.get_by_provides("stream")
    manifest = _build_manifest(plugin_names)

    return JSONResponse(
        content=manifest,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
        },
    )


@router.get("/catalog/{content_type}/{catalog_id}.json")
async def stremio_catalog(
    request: Request,
    content_type: str,
    catalog_id: str,
) -> JSONResponse:
    """Serve Stremio catalog (trending content via TMDB)."""
    state = cast(AppState, request.app.state)
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*",
    }

    if not hasattr(state, "tmdb_client") or state.tmdb_client is None:
        return JSONResponse(content={"metas": []}, headers=headers)

    try:
        if content_type == "movie":
            metas = await state.tmdb_client.trending_movies()
        elif content_type == "series":
            metas = await state.tmdb_client.trending_tv()
        else:
            return JSONResponse(content={"metas": []}, headers=headers)
    except Exception:
        log.warning(
            "stremio_catalog_failed",
            content_type=content_type,
            catalog_id=catalog_id,
            exc_info=True,
        )
        return JSONResponse(content={"metas": []}, headers=headers)

    meta_list = [
        {
            "id": m.id,
            "type": m.type,
            "name": m.name,
            "poster": m.poster,
            "description": m.description,
            "releaseInfo": m.release_info,
            "imdbRating": m.imdb_rating,
            "genres": m.genres,
        }
        for m in metas
    ]

    return JSONResponse(content={"metas": meta_list}, headers=headers)


@router.get("/catalog/{content_type}/{catalog_id}/search={query}.json")
async def stremio_catalog_search(
    request: Request,
    content_type: str,
    catalog_id: str,
    query: str,
) -> JSONResponse:
    """Serve Stremio catalog search results via TMDB."""
    state = cast(AppState, request.app.state)
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*",
    }

    if not hasattr(state, "tmdb_client") or state.tmdb_client is None:
        return JSONResponse(content={"metas": []}, headers=headers)

    if not query.strip():
        return JSONResponse(content={"metas": []}, headers=headers)

    try:
        if content_type == "movie":
            metas = await state.tmdb_client.search_movies(query=query)
        elif content_type == "series":
            metas = await state.tmdb_client.search_tv(query=query)
        else:
            return JSONResponse(content={"metas": []}, headers=headers)
    except Exception:
        log.warning(
            "stremio_catalog_search_failed",
            content_type=content_type,
            query=query,
            exc_info=True,
        )
        return JSONResponse(content={"metas": []}, headers=headers)

    meta_list = [
        {
            "id": m.id,
            "type": m.type,
            "name": m.name,
            "poster": m.poster,
            "description": m.description,
            "releaseInfo": m.release_info,
            "imdbRating": m.imdb_rating,
            "genres": m.genres,
        }
        for m in metas
    ]

    return JSONResponse(content={"metas": meta_list}, headers=headers)


@router.get("/stream/{content_type}/{stream_id}.json")
async def stremio_stream(
    request: Request,
    content_type: str,
    stream_id: str,
) -> JSONResponse:
    """Resolve streams for a movie or episode.

    1. Parse the Stremio stream ID (IMDb ID + optional season/episode).
    2. Lookup title via TMDB (German title preferred).
    3. Search all streaming plugins in parallel (bounded concurrency).
    4. Convert results to RankedStreams, sort, and format for Stremio.
    """
    state = cast(AppState, request.app.state)
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*",
    }

    # 1) Parse stream ID
    parsed = _parse_stream_id(content_type, stream_id)
    if parsed is None:
        return JSONResponse(content={"streams": []}, headers=headers)

    log.info(
        "stremio_stream_request",
        imdb_id=parsed.imdb_id,
        content_type=parsed.content_type,
        season=parsed.season,
        episode=parsed.episode,
    )

    # 2) Lookup title via TMDB
    search_query = await _resolve_search_query(state, parsed)
    if not search_query:
        log.warning("stremio_no_title_found", imdb_id=parsed.imdb_id)
        return JSONResponse(content={"streams": []}, headers=headers)

    # 3) Search all streaming plugins in parallel
    plugin_names = state.plugins.get_by_provides("stream")
    both_names = state.plugins.get_by_provides("both")
    all_names = list(set(plugin_names + both_names))

    if not all_names:
        log.warning("stremio_no_streaming_plugins")
        return JSONResponse(content={"streams": []}, headers=headers)

    category = 2000 if parsed.content_type == "movie" else 5000
    max_concurrent = state.config.stremio.max_concurrent_plugins
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _bounded_search(name: str) -> list[SearchResult]:
        async with semaphore:
            plugin = state.plugins.get(name)
            return await _search_plugin(
                plugin, search_query, state.search_engine, category
            )

    tasks = [_bounded_search(name) for name in all_names]
    results_per_plugin = await asyncio.gather(*tasks)

    all_results: list[SearchResult] = []
    for results in results_per_plugin:
        all_results.extend(results)

    if not all_results:
        log.info(
            "stremio_no_results",
            imdb_id=parsed.imdb_id,
            query=search_query,
        )
        return JSONResponse(content={"streams": []}, headers=headers)

    # 4) Convert, rank, and format
    plugin_languages: dict[str, str] = {}
    for name in all_names:
        plugin = state.plugins.get(name)
        lang = getattr(plugin, "default_language", None)
        if lang:
            plugin_languages[name] = lang

    ranked = convert_search_results(all_results, plugin_languages=plugin_languages)
    sorter = StreamSorter(state.config.stremio)
    sorted_streams = sorter.sort(ranked)

    stremio_streams = [
        _format_stremio_stream(
            StremioStream(
                name=_build_stream_name(s),
                description=_build_description(s),
                url=s.url,
            )
        )
        for s in sorted_streams
    ]

    log.info(
        "stremio_stream_response",
        imdb_id=parsed.imdb_id,
        query=search_query,
        total_results=len(all_results),
        streams_returned=len(stremio_streams),
    )

    return JSONResponse(content={"streams": stremio_streams}, headers=headers)


async def _resolve_search_query(
    state: AppState, parsed: StremioStreamRequest
) -> str | None:
    """Resolve a human-readable search query from an IMDb or TMDB ID via TMDB."""
    if not hasattr(state, "tmdb_client") or state.tmdb_client is None:
        return None

    # Handle tmdb:{id} format — look up directly by TMDB ID
    if parsed.imdb_id.startswith("tmdb:"):
        tmdb_id = parsed.imdb_id.removeprefix("tmdb:")
        title = await state.tmdb_client.get_title_by_tmdb_id(
            int(tmdb_id),
            parsed.content_type,
        )
    else:
        title = await state.tmdb_client.get_german_title(parsed.imdb_id)

    if not title:
        return None

    if parsed.content_type == "series" and parsed.season and parsed.episode:
        return f"{title} S{parsed.season:02d}E{parsed.episode:02d}"

    return title


def _build_stream_name(stream: Any) -> str:
    """Build a human-readable name for a Stremio stream entry.

    Replaces underscores in quality enum names with spaces so that
    ``HD_1080P`` becomes ``HD 1080P`` in the Stremio UI.
    """
    quality_label = stream.quality.name.replace("_", " ")
    name_parts = (
        [stream.source_plugin, quality_label]
        if stream.source_plugin
        else [quality_label]
    )
    return " ".join(name_parts)


def _build_description(stream: Any) -> str:
    """Build a human-readable description line for a Stremio stream."""
    parts: list[str] = []

    if stream.language:
        parts.append(stream.language.label)

    if stream.hoster and stream.hoster != "unknown":
        parts.append(stream.hoster.upper())

    if stream.size:
        parts.append(stream.size)

    return " | ".join(parts) if parts else stream.quality.name
