from __future__ import annotations

from typing import cast
from urllib.parse import urlsplit, urlunsplit

import httpx
import structlog
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from scavengarr.application.use_cases.torznab_caps import TorznabCapsUseCase
from scavengarr.application.use_cases.torznab_indexers import TorznabIndexersUseCase
from scavengarr.application.use_cases.torznab_search import TorznabSearchUseCase
from scavengarr.domain.entities import (
    TorznabBadRequest,
    TorznabExternalError,
    TorznabNoPluginsAvailable,
    TorznabPluginNotFound,
    TorznabQuery,
    TorznabUnsupportedAction,
    TorznabUnsupportedPlugin,
)
from scavengarr.domain.entities.torznab import TorznabItem
from scavengarr.infrastructure.torznab.presenter import render_caps_xml, render_rss_xml
from scavengarr.interfaces.app_state import AppState

log = structlog.get_logger(__name__)

router = APIRouter(tags=["torznab"])


def _xml(payload: bytes, *, status_code: int) -> Response:
    return Response(
        content=payload, media_type="application/xml", status_code=status_code
    )


def _is_prod(state: AppState) -> bool:
    return state.config.environment == "prod"


def _origin_url(base_url: str) -> str:
    """
    Reduce base_url to origin + '/', i.e. scheme://netloc/
    This is the cheapest "is the domain reachable" probe we can do over HTTP(S).
    """
    p = urlsplit(base_url)
    if not p.scheme or not p.netloc:
        # fall back to original; httpx will raise a meaningful error
        return base_url
    return urlunsplit((p.scheme, p.netloc, "/", "", ""))


async def _lightweight_http_probe(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    timeout_seconds: float = 5.0,
) -> tuple[bool, int | None, str | None, str]:
    """
    Lightweight reachability probe:
    - Prefer HEAD (no body)
    - Fallback to GET with Range + stream=True (dont read body) when HEAD is unsupported
    """
    checked_url = _origin_url(base_url)

    try:
        # 1) HEAD is cheapest
        resp = await client.request(
            "HEAD",
            checked_url,
            timeout=timeout_seconds,
            follow_redirects=True,
        )
        if resp.status_code in (405, 501):
            # 2) Some sites do not support HEAD; do a minimal GET
            req = client.build_request(
                "GET",
                checked_url,
                headers={"Range": "bytes=0-0"},
            )
            r = await client.send(
                req, stream=True, timeout=timeout_seconds, follow_redirects=True
            )
            status_code = r.status_code
            await r.aclose()
            return True, status_code, None, checked_url

        return True, resp.status_code, None, checked_url

    except httpx.RequestError as e:
        # DNS/TCP/TLS/timeout etc.
        return False, None, str(e), checked_url


@router.get("/api/v1/torznab/indexers")
async def torznab_indexers(request: Request) -> dict:
    state = cast(AppState, request.app.state)
    uc = TorznabIndexersUseCase(plugins=state.plugins)

    # JSON ist absichtlich: für “Discovery/Automation” (nicht Torznab-Standard).
    return {"indexers": uc.execute()}


@router.get("/api/v1/torznab/{plugin_name}")
async def torznab_plugin_api(
    request: Request,
    plugin_name: str,
    t: str = Query(..., description="Torznab action: caps|search"),
    q: str | None = Query(None, description="Search query"),
    cat: str = Query("", description="Category filter"),
    extended: int | None = Query(None, description="Prowlarr extended search flag"),
) -> Response:
    state = cast(AppState, request.app.state)

    try:
        if t == "caps":
            caps_uc = TorznabCapsUseCase(
                plugins=state.plugins,
                app_name=state.config.app_name,
                plugin_name=plugin_name,
                server_version="0.1.0",
            )
            rendered = render_caps_xml(caps_uc.execute())
            return _xml(rendered.payload, status_code=200)

        if t != "search":
            raise TorznabUnsupportedAction(f"Unsupported action t={t!r}")

        # Special: Prowlarr "Test" calls t=search&extended=1 without q.
        # If extended=1: only succeed (HTTP 200) when the indexer domain is reachable.
        if not q:
            if extended == 1:
                # Load plugin to get base_url (single source of truth)
                state.plugins.discover()
                plugin = state.plugins.get(plugin_name)
                base_url = str(getattr(plugin, "base_url", "") or "")

                if not base_url:
                    rendered = render_rss_xml(
                        title=f"{state.config.app_name} ({plugin_name})",
                        items=[],
                        description="plugin has no base_url"
                        if not _is_prod(state)
                        else None,
                        scavengarr_base_url=str(request.base_url),
                    )
                    return _xml(rendered.payload, status_code=422)

                (
                    reachable,
                    status_code,
                    error,
                    _checked_url,
                ) = await _lightweight_http_probe(
                    state.http_client, base_url=base_url, timeout_seconds=5.0
                )

                if reachable:
                    # Return ONE deterministic test item so Prowlarr "Test" passes.
                    test_item = TorznabItem(
                        title=f"{state.config.app_name} ({plugin_name}) - reachable",
                        download_url=_checked_url,
                        size="0 B",
                        seeders=0,
                        peers=0,
                        category=2000,
                        download_volume_factor=0.0,
                        upload_volume_factor=0.0,
                    )
                    rendered = render_rss_xml(
                        title=f"{state.config.app_name} ({plugin_name})",
                        items=[test_item],
                        description=None,
                        scavengarr_base_url=str(request.base_url),
                    )
                    return _xml(rendered.payload, status_code=200)

                rendered = render_rss_xml(
                    title=f"{state.config.app_name} ({plugin_name})",
                    items=[],
                    description=(error or "indexer not reachable")
                    if not _is_prod(state)
                    else None,
                    scavengarr_base_url=str(request.base_url),
                )
                # 503 makes Prowlarr "Test" fail when the indexer is down (desired)
                return _xml(rendered.payload, status_code=503)

            # Default behavior for missing q (no extended): return valid empty RSS, not 400.
            rendered = render_rss_xml(
                title=f"{state.config.app_name} ({plugin_name})",
                items=[],
                description="Missing query parameter 'q'"
                if not _is_prod(state)
                else None,
                scavengarr_base_url=str(request.base_url),
            )
            return _xml(rendered.payload, status_code=200)

        # Normal search flow (q present)
        search_uc = TorznabSearchUseCase(
            plugins=state.plugins,
            engine=state.search_engine,
            crawljob_factory=state.crawljob_factory,
            crawljob_repo=state.crawljob_repo,
        )
        items = await search_uc.execute(
            TorznabQuery(action="search", query=q, plugin_name=plugin_name)
        )
        rendered = render_rss_xml(
            title=f"{state.config.app_name} ({plugin_name})",
            items=items,
            scavengarr_base_url=str(request.base_url),
        )
        return _xml(rendered.payload, status_code=200)

    except TorznabBadRequest as e:
        rendered = render_rss_xml(
            title=f"{state.config.app_name} ({plugin_name})",
            items=[],
            description=str(e) if not _is_prod(state) else None,
            scavengarr_base_url=str(request.base_url),
        )
        return _xml(rendered.payload, status_code=400)

    except TorznabPluginNotFound:
        rendered = render_rss_xml(
            title=f"{state.config.app_name} ({plugin_name})",
            items=[],
            description="plugin not found" if not _is_prod(state) else None,
            scavengarr_base_url=str(request.base_url),
        )
        return _xml(rendered.payload, status_code=404)

    except TorznabNoPluginsAvailable:
        rendered = render_rss_xml(
            title=f"{state.config.app_name} ({plugin_name})",
            items=[],
            description="no plugins available" if not _is_prod(state) else None,
            scavengarr_base_url=str(request.base_url),
        )
        return _xml(rendered.payload, status_code=503)

    except (TorznabUnsupportedAction, TorznabUnsupportedPlugin) as e:
        rendered = render_rss_xml(
            title=f"{state.config.app_name} ({plugin_name})",
            items=[],
            description=str(e) if not _is_prod(state) else None,
            scavengarr_base_url=str(request.base_url),
        )
        return _xml(rendered.payload, status_code=422)

    except TorznabExternalError as e:
        # "prod": stabil für Prowlarr -> leeres RSS (200)
        status = 200 if _is_prod(state) else 502
        rendered = render_rss_xml(
            title=f"{state.config.app_name} ({plugin_name})",
            items=[],
            description=str(e) if not _is_prod(state) else None,
            scavengarr_base_url=str(request.base_url),
        )
        return _xml(rendered.payload, status_code=status)

    except Exception:
        # Unknown error: prod -> leeres RSS (200), dev/test -> 500 mit Hinweis
        status = 200 if _is_prod(state) else 500
        rendered = render_rss_xml(
            title=f"{state.config.app_name} ({plugin_name})",
            items=[],
            description="internal error" if not _is_prod(state) else None,
            scavengarr_base_url=str(request.base_url),
        )
        log.exception("torznab_unhandled_error", plugin_name=plugin_name, t=t)
        return _xml(rendered.payload, status_code=status)


@router.get("/api/v1/torznab/indexers")
async def torznab_indexers(request: Request) -> dict:
    state = cast(AppState, request.app.state)
    uc = TorznabIndexersUseCase(plugins=state.plugins)
    return {"indexers": uc.execute()}


@router.get("/api/v1/torznab/{plugin_name}/health")
async def torznab_plugin_health(request: Request, plugin_name: str) -> JSONResponse:
    """
    Lightweight reachability check for the plugin's domain/base_url.
    Uses the plugin registry directly because TorznabIndexersUseCase does not expose base_url.
    """
    state = cast(AppState, request.app.state)

    try:
        state.plugins.discover()
        plugin = state.plugins.get(plugin_name)
    except TorznabPluginNotFound:
        return JSONResponse(
            status_code=404,
            content={
                "plugin": plugin_name,
                "reachable": False,
                "error": "plugin not found",
            },
        )
    except Exception as e:
        # keep endpoint resilient
        return JSONResponse(
            status_code=500 if not _is_prod(state) else 200,
            content={
                "plugin": plugin_name,
                "reachable": False,
                "error": str(e) if not _is_prod(state) else "internal error",
            },
        )

    base_url = str(getattr(plugin, "base_url", None) or "")
    if not base_url:
        return JSONResponse(
            status_code=422,
            content={
                "plugin": plugin_name,
                "reachable": False,
                "error": "plugin has no base_url",
            },
        )

    reachable, status_code, error, checked_url = await _lightweight_http_probe(
        state.http_client, base_url=base_url, timeout_seconds=5.0
    )

    return JSONResponse(
        status_code=200,
        content={
            "plugin": plugin_name,
            "base_url": base_url,
            "checked_url": checked_url,
            "reachable": reachable,
            "status_code": status_code,
            "error": error,
        },
    )
