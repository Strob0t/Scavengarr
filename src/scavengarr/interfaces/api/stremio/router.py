"""Stremio addon API endpoints (manifest, catalog, stream, play, HLS proxy)."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any, cast
from urllib.parse import urlparse

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.responses import StreamingResponse

from scavengarr.application.stremio.stream_builder import FILE_NAME, HLS_MASTER
from scavengarr.application.use_cases.stremio_links import StremioLinks
from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    StremioContentType,
    StremioMetaPreview,
    StremioStream,
    StremioStreamRequest,
)
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort
from scavengarr.infrastructure.hoster_resolvers._domain import extract_domain
from scavengarr.infrastructure.stremio.hls_proxy import (
    build_cdn_url,
    cdn_base_from_url,
    fetch_hls_resource,
    rewrite_manifest,
    stream_file,
    stream_hls_segment,
)
from scavengarr.infrastructure.version import APP_VERSION, build_identity
from scavengarr.interfaces.app_state import AppState

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/stremio", tags=["stremio"])

_ADDON_ID = "community.scavengarr"

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
}

# The stream response's X-Cache header per answer source: a fresh search, a
# cache entry, a stale entry (refreshed in the background), a shared wait
# for the title's running search; "none" ended before any search
_CACHE_STATUS = {
    "search": "MISS",
    "cache": "HIT",
    "stale": "STALE",
    "joined": "JOINED",
    "none": "MISS",
}

# FFmpeg's own User-Agent: Stremio's streaming server probes and converts with it
_FFMPEG_AGENT = "Lavf/"


def _catalogs(*, trending: bool) -> list[dict[str, Any]]:
    """The addon's catalogs: trending rows, searchable.

    Without trending (no TMDB key, only the search works) they are
    search-only, so Stremio lists them in its search results instead of
    showing empty rows on its board.
    """
    search = [{"name": "search", "isRequired": not trending}]
    label = "Trending " if trending else ""
    return [
        {
            "type": "movie",
            "id": "scavengarr-trending-movies",
            "name": f"Scavengarr {label}Movies",
            "extra": search,
        },
        {
            "type": "series",
            "id": "scavengarr-trending-series",
            "name": f"Scavengarr {label}Series",
            "extra": search,
        },
    ]


def _build_manifest(catalogs: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the Stremio addon manifest."""
    return {
        "id": _ADDON_ID,
        "version": APP_VERSION,
        "name": "Scavengarr",
        "description": "German streaming links from multiple sources",
        "types": ["movie", "series"],
        "catalogs": catalogs,
        "resources": ["catalog", "stream"] if catalogs else ["stream"],
        "idPrefixes": ["tt", "tmdb:", "kitsu:"],
        "behaviorHints": {
            "adult": False,
            "configurable": False,
        },
    }


def _parse_stream_id(content_type: str, raw_id: str) -> StremioStreamRequest | None:
    """Parse Stremio stream ID into a StremioStreamRequest.

    Movies: "tt1234567", "tmdb:12345" or "kitsu:11614"
    Series: "tt1234567:1:5" or "tmdb:12345:1:5" (season 1, episode 5),
    "kitsu:41982:3" (episode 3 as Kitsu counts, no season)
    """
    if content_type not in ("movie", "series"):
        return None

    ct: StremioContentType = cast(StremioContentType, content_type)

    # Handle kitsu:{id} format (the Anime Kitsu addon's catalogs)
    if raw_id.startswith("kitsu:"):
        return _parse_kitsu_id(ct, raw_id)

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


def _parse_kitsu_id(ct: StremioContentType, raw_id: str) -> StremioStreamRequest | None:
    """``kitsu:41982`` or ``kitsu:41982:3``: the episode is Kitsu's count
    (no season); the stream use case translates the request."""
    parts = raw_id.split(":")
    episode: int | None = None
    if len(parts) == 3:
        try:
            episode = int(parts[2])
        except ValueError:
            return None
    elif len(parts) != 2:
        return None
    return StremioStreamRequest(
        imdb_id=f"kitsu:{parts[1]}", content_type=ct, episode=episode
    )


def _format_stremio_stream(stream: StremioStream) -> dict[str, Any]:
    """Convert a StremioStream dataclass to Stremio JSON format."""
    data: dict[str, Any] = {
        "name": stream.name,
        "description": stream.description,
        "url": stream.url,
    }
    if stream.behavior_hints:
        data["behaviorHints"] = stream.behavior_hints
    return data


def _empty_metas() -> JSONResponse:
    """Return an empty Stremio catalog response."""
    return JSONResponse(content={"metas": []}, headers=_CORS_HEADERS)


def _empty_streams() -> JSONResponse:
    """Return an empty Stremio streams response."""
    return JSONResponse(content={"streams": []}, headers=_CORS_HEADERS)


def _error_json(status: int, message: str) -> JSONResponse:
    """Return a JSON error response with CORS headers."""
    return JSONResponse(
        status_code=status,
        content={"error": message},
        headers=_CORS_HEADERS,
    )


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
    catalog_uc = getattr(state, "stremio_catalog_uc", None)
    catalogs = [] if catalog_uc is None else _catalogs(trending=catalog_uc.has_trending)
    manifest = _build_manifest(catalogs)

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
        return _empty_metas()

    if content_type not in ("movie", "series"):
        return _empty_metas()

    ct = cast(StremioContentType, content_type)

    try:
        metas = await uc.trending(ct)
    except Exception:
        log.exception(
            "stremio_catalog_error",
            content_type=content_type,
            catalog_id=catalog_id,
        )
        return _empty_metas()

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
        return _empty_metas()

    if content_type not in ("movie", "series"):
        return _empty_metas()

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
        return _empty_metas()

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
        return _empty_streams()

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
        return _empty_streams()

    try:
        answer = await uc.answer(parsed, base_url=str(request.base_url).rstrip("/"))
    except Exception:
        log.exception(
            "stremio_stream_error",
            imdb_id=parsed.imdb_id,
            content_type=parsed.content_type,
            season=parsed.season,
            episode=parsed.episode,
        )
        return _empty_streams()

    stremio_streams = [_format_stremio_stream(s) for s in answer.streams]

    log.info(
        "stremio_stream_response",
        imdb_id=parsed.imdb_id,
        streams_returned=len(stremio_streams),
        source=answer.source,
        complete=answer.complete,
        missing_count=len(answer.missing),
    )

    # Where the answer came from and whether a search still runs for it
    # (no plugin missing, no continuation): what a retry can expect
    headers = {
        **_CORS_HEADERS,
        "X-Cache": _CACHE_STATUS[answer.source],
        "X-Search-Complete": "true" if answer.complete else "false",
    }
    return JSONResponse(content={"streams": stremio_streams}, headers=headers)


@router.api_route("/play/{stream_id}", methods=["GET", "HEAD"], response_model=None)
async def stremio_play(
    request: Request,
    stream_id: str,
) -> JSONResponse | RedirectResponse:
    """Redirect to the current video URL of a stream link.

    Flow:
        1. Look up the stored link by stream_id (404 when missing).
        2. Take its video URL while fresh, else resolve the hoster URL
           again (``StremioLinks``: autoplay and "Continue Watching" play a
           stream object Stremio kept for an hour or for days). A hoster
           whose CDN binds the video URL to the player's headers (VEEV)
           resolves for this request's headers: the player sends the CDN the
           same ones after the redirect.
        3. Redirect to the video URL (302); 502 when the hoster gives no
           video (never a redirect to an embed page).

    HEAD answers like GET: a streaming server asks with HEAD first.
    """
    state = cast(AppState, request.app.state)

    links = getattr(state, "stremio_links", None)
    if links is None:
        return _error_json(503, "stream links not configured")

    link = await links.get(stream_id)
    if link is None:
        log.warning("stremio_play_not_found", stream_id=stream_id)
        return _error_json(404, "stream expired or not found")

    current = await links.current(link, request.headers)
    if current is None:
        log.warning(
            "stremio_play_resolution_failed",
            stream_id=stream_id,
            hoster=link.hoster,
            url=link.hoster_url,
        )
        return _error_json(502, "could not extract video URL from hoster")

    log.info(
        "stremio_play_resolved",
        stream_id=stream_id,
        hoster=link.hoster,
        cdn=extract_domain(current.video_url),
        is_hls=current.is_hls,
    )
    return RedirectResponse(
        url=current.video_url,
        status_code=302,
        headers=_CORS_HEADERS,
    )


def _resolve_query_string(request_query: str, video_url: str) -> str:
    """Return query string for CDN request, falling back to original URL."""
    qs = request_query or ""
    if not qs:
        original_qs = urlparse(video_url).query
        if original_qs:
            return original_qs
    return qs


def _cdn_error_response(
    stream_id: str, target_url: str, exc: httpx.HTTPError
) -> JSONResponse:
    """Map a CDN httpx error to a 502 JSON response."""
    if isinstance(exc, httpx.HTTPStatusError):
        log.warning(
            "hls_proxy_cdn_error",
            stream_id=stream_id,
            status=exc.response.status_code,
            cdn=extract_domain(target_url),
        )
        return _error_json(502, "CDN returned error")
    log.warning(
        "hls_proxy_network_error",
        stream_id=stream_id,
        cdn=extract_domain(target_url),
    )
    return _error_json(502, "CDN request failed")


@router.api_route(
    "/proxy/{stream_id}/{path:path}", methods=["GET", "HEAD"], response_model=None
)
async def proxy_hls(
    stream_id: str,
    path: str,
    request: Request,
) -> Response | JSONResponse:
    """Proxy HLS manifests and segments with correct CDN headers.

    Some CDNs (e.g. Dropload's ``dropcdn.io``) require ``Referer`` on
    **every** HLS sub-request.  Stremio's ``proxyHeaders`` only applies
    headers to the initial manifest fetch, so variant playlists and
    ``.ts`` segments get 403.  This endpoint fetches resources
    server-side with the stored headers and rewrites manifest URLs so
    the HLS player routes subsequent requests through the proxy too.

    The stream's playlist has a fixed path (``HLS_MASTER``): the proxy
    serves the current one, resolved again when stale or refused by the
    CDN, so a stream object Stremio kept still plays later.

    HEAD answers like GET (the server drops the body): Stremio Web reads
    the stream's content type with a HEAD request before it plays. A
    segment is not downloaded for HEAD.

    An address-bound file (``FILE_NAME``; its CDN plays it only for the
    address that resolved it) is streamed with the player's byte range
    (``_proxy_file``).

    The playlist is not served to a streaming server's converter unless
    ``stremio.allow_hls_transcoding`` is on (``_converter_refused``).

    Each request is recorded by kind with its answer's status, until the
    answer starts; segments with the bytes sent.
    """
    state = cast(AppState, request.app.state)
    telemetry: TelemetryPort = getattr(state, "telemetry", NO_TELEMETRY)
    kind = _hls_kind(path)
    with telemetry.stage("hls_proxy", kind=kind) as stage:
        response = await _proxy_hls(state, telemetry, stream_id, path, request)
        stage.outcome = str(response.status_code)
    if (
        request.method == "GET"
        and response.status_code == 200
        and not isinstance(response, StreamingResponse)
    ):
        telemetry.record("hls_proxy_bytes", len(response.body), kind=kind)
    return response


def _hls_kind(path: str) -> str:
    """The stream's own playlist (``master``), an address-bound ``file``,
    another ``playlist`` or a ``segment``."""
    if path == HLS_MASTER:
        return "master"
    if path == FILE_NAME:
        return "file"
    return "playlist" if path.endswith(".m3u8") else "segment"


async def _counted(
    chunks: AsyncGenerator[bytes],
    telemetry: TelemetryPort,
    *,
    kind: str = "segment",
) -> AsyncGenerator[bytes]:
    """A segment's or file's chunks; the bytes sent are recorded as *kind*
    when it ends, an aborted transfer's included."""
    sent = 0
    try:
        async for chunk in chunks:
            sent += len(chunk)
            yield chunk
    finally:
        await chunks.aclose()
        telemetry.record("hls_proxy_bytes", sent, kind=kind)


async def _proxy_hls(
    state: AppState,
    telemetry: TelemetryPort,
    stream_id: str,
    path: str,
    request: Request,
) -> Response | JSONResponse:
    links = getattr(state, "stremio_links", None)
    if links is None:
        return _error_json(503, "stream links not configured")
    if path == FILE_NAME:
        return await _proxy_file(state, telemetry, links, stream_id, request)
    master = path == HLS_MASTER
    if master and _converter_refused(state, request):
        log.info("hls_proxy_converter_refused", stream_id=stream_id)
        return _error_json(403, "HLS streams play in the player, not transcoded")

    link = await _proxy_link(links, stream_id, master=master)
    if isinstance(link, JSONResponse):
        return link

    if master:
        target_url = link.video_url
    else:
        query_string = _resolve_query_string(request.url.query or "", link.video_url)
        try:
            target_url = build_cdn_url(
                cdn_base_from_url(link.video_url), path, query_string
            )
        except ValueError:
            log.warning("hls_proxy_foreign_path", stream_id=stream_id, path=path[:80])
            return _error_json(400, "path outside the stream's CDN")

    # Segments (.ts) — stream without buffering full body
    if not master and not path.endswith(".m3u8"):
        head = request.method == "HEAD"
        try:
            chunk_iter, content_type = await stream_hls_segment(
                state.http_client, target_url, _cdn_headers(link), head=head
            )
        except httpx.HTTPError as exc:
            return _cdn_error_response(stream_id, target_url, exc)
        return StreamingResponse(
            content=chunk_iter if head else _counted(chunk_iter, telemetry),
            media_type=content_type,
            headers=_CORS_HEADERS,
        )

    # Manifests (.m3u8) — fetch, rewrite URLs, return
    fetched = await _fetch_playlist(
        state.http_client, links, link, target_url, master=master
    )
    if isinstance(fetched, JSONResponse):
        return fetched
    body, link = fetched

    # What the playlist lists goes to a copy of its link: a later resolution
    # under the stream's id leaves a running playback alone
    link = await links.pinned(link)
    proxy_base = (
        f"{str(request.base_url).rstrip('/')}/api/v1/stremio/proxy/{link.stream_id}/"
    )
    rewritten = rewrite_manifest(
        body.decode("utf-8", errors="replace"),
        cdn_base_from_url(link.video_url),
        proxy_base,
        playlist_dir="" if master else path[: path.rfind("/") + 1],
    )
    return Response(
        content=rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers=_CORS_HEADERS,
    )


def _converter_refused(state: AppState, request: Request) -> bool:
    """Whether *request* is a streaming server's ffmpeg that may not transcode.

    Stremio Web has its streaming server probe every stream before it plays
    it. An HLS source (format ``hls``) then goes through the server's
    converter, which re-encodes the video: it repackages MP4 and Matroska
    only. On a Raspberry Pi 4 without a usable hardware encoder 1080p
    stuttered at 1-2 cores. A failed probe makes Stremio Web read the
    content type with HEAD and play the playlist itself (hls.js).
    """
    agent = request.headers.get("user-agent", "")
    return (
        agent.startswith(_FFMPEG_AGENT)
        and not state.config.stremio.allow_hls_transcoding
    )


async def _proxy_file(
    state: AppState,
    telemetry: TelemetryPort,
    links: StremioLinks,
    stream_id: str,
    request: Request,
) -> Response | JSONResponse:
    """An address-bound file (``FILE_NAME``): the current video URL streamed
    with the player's byte range, the CDN's status and headers passed on.

    When the CDN refuses the file (403, 404, 410: an expired URL), it is
    fetched once more from the link ``StremioLinks.after_refusal`` gives,
    then 502, as for the stream's playlist.
    """
    link = await _file_link(links, stream_id)
    if isinstance(link, JSONResponse):
        return link
    head = request.method == "HEAD"
    try:
        answer = await stream_file(
            state.http_client,
            link.video_url,
            _cdn_headers(link),
            player=request.headers,
            head=head,
        )
    except httpx.HTTPStatusError as exc:
        fresh = await links.after_refusal(link, exc.response.status_code)
        if fresh is None or fresh.is_hls:
            return _cdn_error_response(stream_id, link.video_url, exc)
        try:
            answer = await stream_file(
                state.http_client,
                fresh.video_url,
                _cdn_headers(fresh),
                player=request.headers,
                head=head,
            )
        except httpx.HTTPError as again:
            return _cdn_error_response(stream_id, fresh.video_url, again)
    except httpx.HTTPError as exc:
        return _cdn_error_response(stream_id, link.video_url, exc)
    return StreamingResponse(
        content=answer.chunks
        if head
        else _counted(answer.chunks, telemetry, kind="file"),
        status_code=answer.status,
        headers={**_CORS_HEADERS, **answer.headers},
    )


async def _stored_link(
    links: StremioLinks, stream_id: str, *, current: bool
) -> CachedStreamLink | JSONResponse:
    """The stream's stored link; the *current* one (resolved again when
    stale) for the stream's playlist and for a file."""
    link = await links.get(stream_id)
    if link is None:
        log.warning("hls_proxy_not_found", stream_id=stream_id)
        return _error_json(404, "stream expired or not found")
    if not current:
        return link
    fresh = await links.current(link)
    if fresh is None:
        log.warning("hls_proxy_resolution_failed", stream_id=stream_id)
        return _error_json(502, "could not extract video URL from hoster")
    return fresh


async def _proxy_link(
    links: StremioLinks, stream_id: str, *, master: bool
) -> CachedStreamLink | JSONResponse:
    """The stream's link for a playlist or segment request; for the stream's
    playlist the current one.

    Other paths (variants, segments) follow a playlist fetched moments
    before and keep its link.
    """
    link = await _stored_link(links, stream_id, current=master)
    if isinstance(link, JSONResponse):
        return link
    if not link.video_url or not link.is_hls:
        log.warning("hls_proxy_not_hls", stream_id=stream_id)
        return _error_json(400, "stream is not an HLS proxy stream")
    return link


async def _file_link(
    links: StremioLinks, stream_id: str
) -> CachedStreamLink | JSONResponse:
    """The current link of an address-bound file; 400 for an HLS stream or
    a file the builder sends through ``/play`` (a record from before the
    flag among them)."""
    link = await _stored_link(links, stream_id, current=True)
    if isinstance(link, JSONResponse):
        return link
    if not link.video_url or link.is_hls or not link.address_bound:
        log.warning("hls_proxy_not_a_file", stream_id=stream_id)
        return _error_json(400, "stream is not a proxied file")
    return link


async def _fetch_playlist(
    http_client: httpx.AsyncClient,
    links: StremioLinks,
    link: CachedStreamLink,
    target_url: str,
    *,
    master: bool,
) -> tuple[bytes, CachedStreamLink] | JSONResponse:
    """A playlist's body, and the link it came with.

    When the CDN refuses the stream's own playlist, it is fetched once
    more from the link ``StremioLinks.after_refusal`` gives (an expired
    token).
    """
    try:
        body, _ = await fetch_hls_resource(http_client, target_url, _cdn_headers(link))
        return body, link
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        fresh = await links.after_refusal(link, status) if master else None
        if fresh is None or not fresh.is_hls:
            return _cdn_error_response(link.stream_id, target_url, exc)
    except httpx.HTTPError as exc:
        return _cdn_error_response(link.stream_id, target_url, exc)

    try:
        body, _ = await fetch_hls_resource(
            http_client, fresh.video_url, _cdn_headers(fresh)
        )
    except httpx.HTTPError as exc:
        return _cdn_error_response(link.stream_id, fresh.video_url, exc)
    return body, fresh


def _cdn_headers(link: CachedStreamLink) -> dict[str, str]:
    """The headers the stream's CDN wants (stored as JSON)."""
    if not link.video_headers:
        return {}
    try:
        return json.loads(link.video_headers)
    except (json.JSONDecodeError, ValueError):
        log.warning("hls_proxy_bad_headers", stream_id=link.stream_id)
        return {}


@router.get("/health")
async def stremio_health(request: Request) -> JSONResponse:
    """Report Stremio addon health and component status."""
    state = cast(AppState, request.app.state)

    tmdb_configured = getattr(state, "tmdb_client", None) is not None
    anime_ids_configured = getattr(state, "anime_ids", None) is not None
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
        supported_hosters = list(resolver_registry.supported_hosters)

    healthy = (
        tmdb_configured
        and stream_uc is not None
        and catalog_uc is not None
        and resolver_registry is not None
        and stream_link_repo is not None
        and len(stream_plugin_names) > 0
    )

    metrics_snapshot: dict[str, object] = {}
    telemetry = getattr(state, "telemetry", None)
    if telemetry is not None:
        metrics_snapshot = telemetry.snapshot()

    content: dict[str, object] = {
        "healthy": healthy,
        **build_identity(),
        "tmdb_configured": tmdb_configured,
        "anime_ids_configured": anime_ids_configured,
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
