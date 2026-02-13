"""Stremio addon API endpoints (manifest, catalog, stream, play)."""

from __future__ import annotations

from typing import Any, cast

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

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

    try:
        metas = await uc.trending(ct)
    except Exception:
        log.exception(
            "stremio_catalog_error",
            content_type=content_type,
            catalog_id=catalog_id,
        )
        return JSONResponse(content={"metas": []}, headers=_CORS_HEADERS)

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

    try:
        metas = await uc.search(ct, query)
    except Exception:
        log.exception(
            "stremio_catalog_search_error",
            content_type=content_type,
            catalog_id=catalog_id,
            query=query,
        )
        return JSONResponse(content={"metas": []}, headers=_CORS_HEADERS)

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

    try:
        streams = await uc.execute(parsed, base_url=str(request.base_url).rstrip("/"))
    except Exception:
        log.exception(
            "stremio_stream_error",
            imdb_id=parsed.imdb_id,
            content_type=parsed.content_type,
            season=parsed.season,
            episode=parsed.episode,
        )
        return JSONResponse(content={"streams": []}, headers=_CORS_HEADERS)

    stremio_streams = [_format_stremio_stream(s) for s in streams]

    log.info(
        "stremio_stream_response",
        imdb_id=parsed.imdb_id,
        streams_returned=len(stremio_streams),
    )

    return JSONResponse(content={"streams": stremio_streams}, headers=_CORS_HEADERS)


@router.get("/play/{stream_id}", response_model=None)
async def stremio_play(
    request: Request,
    stream_id: str,
) -> JSONResponse | RedirectResponse:
    """Resolve a cached stream link to a playable video URL.

    Flow:
        1. Look up the cached hoster URL by stream_id.
        2. Use HosterResolverRegistry to extract the actual video URL.
        3. Redirect to the resolved video URL (302).
        4. Return 502 if resolution fails (never redirect to an embed page).
    """
    state = cast(AppState, request.app.state)

    repo = getattr(state, "stream_link_repo", None)
    if repo is None:
        return JSONResponse(
            status_code=503,
            content={"error": "stream link repository not configured"},
            headers=_CORS_HEADERS,
        )

    link = await repo.get(stream_id)
    if link is None:
        log.warning("stremio_play_not_found", stream_id=stream_id)
        return JSONResponse(
            status_code=404,
            content={"error": "stream expired or not found"},
            headers=_CORS_HEADERS,
        )

    # Resolve hoster embed URL to actual video URL
    registry = getattr(state, "hoster_resolver_registry", None)
    if registry is None:
        log.warning("stremio_play_no_resolver", stream_id=stream_id)
        return JSONResponse(
            status_code=503,
            content={"error": "hoster resolver not configured"},
            headers=_CORS_HEADERS,
        )

    resolved = await registry.resolve(link.hoster_url, hoster=link.hoster)
    if resolved is None:
        log.warning(
            "stremio_play_resolution_failed",
            stream_id=stream_id,
            hoster=link.hoster,
            url=link.hoster_url,
        )
        return JSONResponse(
            status_code=502,
            content={"error": "could not extract video URL from hoster"},
            headers=_CORS_HEADERS,
        )

    log.info(
        "stremio_play_resolved",
        stream_id=stream_id,
        hoster=link.hoster,
        video_url=resolved.video_url[:80],
        is_hls=resolved.is_hls,
    )
    return RedirectResponse(
        url=resolved.video_url,
        status_code=302,
        headers=_CORS_HEADERS,
    )


@router.get("/health")
async def stremio_health(request: Request) -> JSONResponse:
    """Report Stremio addon health and component status."""
    state = cast(AppState, request.app.state)

    tmdb_configured = getattr(state, "tmdb_client", None) is not None
    stream_uc = getattr(state, "stremio_stream_uc", None)
    catalog_uc = getattr(state, "stremio_catalog_uc", None)
    resolver_registry = getattr(state, "hoster_resolver_registry", None)
    stream_link_repo = getattr(state, "stream_link_repo", None)

    stream_plugin_names: list[str] = []
    try:
        stream_plugin_names = state.plugins.get_by_provides("stream")
    except Exception:
        log.warning("stremio_health_plugin_error", exc_info=True)

    supported_hosters: list[str] = []
    if resolver_registry is not None:
        try:
            supported_hosters = list(resolver_registry.list_hosters())
        except Exception:
            pass

    healthy = (
        tmdb_configured
        and stream_uc is not None
        and catalog_uc is not None
        and resolver_registry is not None
        and stream_link_repo is not None
        and len(stream_plugin_names) > 0
    )

    metrics_snapshot: dict[str, object] = {}
    metrics = getattr(state, "metrics", None)
    if metrics is not None:
        metrics_snapshot = metrics.snapshot()

    content: dict[str, object] = {
        "healthy": healthy,
        "tmdb_configured": tmdb_configured,
        "stream_plugin_count": len(stream_plugin_names),
        "stream_plugins": stream_plugin_names,
        "stream_uc_initialized": stream_uc is not None,
        "catalog_uc_initialized": catalog_uc is not None,
        "hoster_resolver_configured": resolver_registry is not None,
        "supported_hosters": supported_hosters,
        "stream_link_repo_configured": stream_link_repo is not None,
        "metrics": metrics_snapshot,
    }

    return JSONResponse(
        status_code=200 if healthy else 503,
        content=content,
    )
