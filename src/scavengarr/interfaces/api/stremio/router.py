"""Stremio addon API endpoints (manifest, catalog, stream)."""

from __future__ import annotations

from typing import Any, cast

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from scavengarr.domain.entities.stremio import (
    StremioContentType,
    StremioMetaPreview,
    StremioStream,
    StremioStreamRequest,
)
from scavengarr.interfaces.app_state import AppState

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/stremio", tags=["stremio"])

_ADDON_ID = "community.scavengarr"
_ADDON_VERSION = "0.1.0"

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
}


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


def _format_meta_preview(m: StremioMetaPreview) -> dict[str, Any]:
    """Convert a StremioMetaPreview to Stremio JSON format."""
    return {
        "id": m.id,
        "type": m.type,
        "name": m.name,
        "poster": m.poster,
        "description": m.description,
        "releaseInfo": m.release_info,
        "imdbRating": m.imdb_rating,
        "genres": m.genres,
    }


@router.get("/manifest.json")
async def stremio_manifest(request: Request) -> JSONResponse:
    """Serve the Stremio addon manifest."""
    state = cast(AppState, request.app.state)
    plugin_names = state.plugins.get_by_provides("stream")
    manifest = _build_manifest(plugin_names)

    return JSONResponse(content=manifest, headers=_CORS_HEADERS)


@router.get("/catalog/{content_type}/{catalog_id}.json")
async def stremio_catalog(
    request: Request,
    content_type: str,
    catalog_id: str,
) -> JSONResponse:
    """Serve Stremio catalog (trending content via TMDB)."""
    state = cast(AppState, request.app.state)

    uc = getattr(state, "stremio_catalog_uc", None)
    if uc is None:
        return JSONResponse(content={"metas": []}, headers=_CORS_HEADERS)

    if content_type not in ("movie", "series"):
        return JSONResponse(content={"metas": []}, headers=_CORS_HEADERS)

    ct = cast(StremioContentType, content_type)
    metas = await uc.trending(ct)
    meta_list = [_format_meta_preview(m) for m in metas]

    return JSONResponse(content={"metas": meta_list}, headers=_CORS_HEADERS)


@router.get("/catalog/{content_type}/{catalog_id}/search={query}.json")
async def stremio_catalog_search(
    request: Request,
    content_type: str,
    catalog_id: str,
    query: str,
) -> JSONResponse:
    """Serve Stremio catalog search results via TMDB."""
    state = cast(AppState, request.app.state)

    uc = getattr(state, "stremio_catalog_uc", None)
    if uc is None:
        return JSONResponse(content={"metas": []}, headers=_CORS_HEADERS)

    if content_type not in ("movie", "series"):
        return JSONResponse(content={"metas": []}, headers=_CORS_HEADERS)

    ct = cast(StremioContentType, content_type)
    metas = await uc.search(ct, query)
    meta_list = [_format_meta_preview(m) for m in metas]

    return JSONResponse(content={"metas": meta_list}, headers=_CORS_HEADERS)


@router.get("/stream/{content_type}/{stream_id}.json")
async def stremio_stream(
    request: Request,
    content_type: str,
    stream_id: str,
) -> JSONResponse:
    """Resolve streams for a movie or episode.

    1. Parse the Stremio stream ID (IMDb ID + optional season/episode).
    2. Delegate to StremioStreamUseCase for title lookup, plugin search,
       ranking, and formatting.
    """
    state = cast(AppState, request.app.state)

    # 1) Parse stream ID
    parsed = _parse_stream_id(content_type, stream_id)
    if parsed is None:
        return JSONResponse(content={"streams": []}, headers=_CORS_HEADERS)

    log.info(
        "stremio_stream_request",
        imdb_id=parsed.imdb_id,
        content_type=parsed.content_type,
        season=parsed.season,
        episode=parsed.episode,
    )

    # 2) Delegate to use case
    uc = getattr(state, "stremio_stream_uc", None)
    if uc is None:
        return JSONResponse(content={"streams": []}, headers=_CORS_HEADERS)

    streams = await uc.execute(parsed, base_url=str(request.base_url).rstrip("/"))
    stremio_streams = [_format_stremio_stream(s) for s in streams]

    log.info(
        "stremio_stream_response",
        imdb_id=parsed.imdb_id,
        streams_returned=len(stremio_streams),
    )

    return JSONResponse(content={"streams": stremio_streams}, headers=_CORS_HEADERS)
