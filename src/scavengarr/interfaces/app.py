"""FastAPI application factory (create_app)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from scavengarr.infrastructure.config import AppConfig
from scavengarr.infrastructure.graceful_shutdown import GracefulShutdown
from scavengarr.interfaces.api.middleware import RateLimitMiddleware
from scavengarr.interfaces.app_state import AppState
from scavengarr.interfaces.composition import lifespan

log = structlog.get_logger(__name__)


def create_app(config: AppConfig) -> FastAPI:
    """Create FastAPI app — configuration ONLY, NO resource initialization.

    Resources (HTTP client, cache, plugins) are created in lifespan().
    """
    app = FastAPI(
        title="Scavengarr",
        description="Prowlarr-compatible Torznab/Newznab indexer",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state = AppState()
    app.state.config = config
    app.state.graceful_shutdown = GracefulShutdown()

    # API rate limiting (per-IP sliding window)
    if config.api_rate_limit_rpm > 0:
        app.add_middleware(
            RateLimitMiddleware, requests_per_minute=config.api_rate_limit_rpm
        )

    from scavengarr.interfaces.api.download.router import router as download_router
    from scavengarr.interfaces.api.stats import router as stats_router
    from scavengarr.interfaces.api.stremio import router as stremio_router
    from scavengarr.interfaces.api.torznab import router as torznab_router

    app.include_router(download_router, prefix="/api/v1")
    app.include_router(torznab_router, prefix="/api/v1")
    app.include_router(stremio_router, prefix="/api/v1")
    app.include_router(stats_router, prefix="/api/v1")

    @app.get("/api/v1/healthz")
    async def healthz() -> dict[str, str | int | list[str]]:
        """Liveness probe — returns 200 as long as the process is running."""
        state = app.state
        plugins = getattr(state, "plugins", None)
        registry = getattr(state, "hoster_resolver_registry", None)
        return {
            "status": "ok",
            "plugins": len(plugins.list_names()) if plugins else 0,
            "hosters": registry.supported_hosters if registry else [],
        }

    @app.get("/api/v1/readyz")
    async def readyz() -> Response:
        """Readiness probe — 200 after startup complete, 503 otherwise."""
        gs: GracefulShutdown = app.state.graceful_shutdown
        if gs.is_ready:
            return JSONResponse({"status": "ready"}, status_code=200)
        return JSONResponse({"status": "not_ready"}, status_code=503)

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ):
        gs: GracefulShutdown = app.state.graceful_shutdown
        gs.request_started()
        start = time.perf_counter()
        try:
            response = await call_next(request)
            return response
        finally:
            gs.request_finished()
            duration_ms = (time.perf_counter() - start) * 1000.0
            status_code = getattr(locals().get("response", None), "status_code", 500)

            log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                query=str(request.url.query),
                status_code=status_code,
                duration_ms=round(duration_ms, 2),
                client_host=(request.client.host if request.client else None),
            )

    return app
