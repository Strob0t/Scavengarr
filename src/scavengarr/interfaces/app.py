"""FastAPI application factory (create_app)."""

from __future__ import annotations

import time

import structlog
from fastapi import FastAPI, Request

from scavengarr.infrastructure.config import AppConfig
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

    # API rate limiting (per-IP sliding window)
    if config.api_rate_limit_rpm > 0:
        app.add_middleware(
            RateLimitMiddleware, requests_per_minute=config.api_rate_limit_rpm
        )

    from scavengarr.interfaces.api.download.router import router as download_router
    from scavengarr.interfaces.api.stremio import router as stremio_router
    from scavengarr.interfaces.api.torznab import router as torznab_router

    app.include_router(download_router, prefix="/api/v1")
    app.include_router(torznab_router, prefix="/api/v1")
    app.include_router(stremio_router, prefix="/api/v1")

    @app.get("/api/v1/healthz")
    async def healthz() -> dict[str, str | int | list[str]]:
        state = app.state
        plugins = getattr(state, "plugins", None)
        registry = getattr(state, "hoster_resolver_registry", None)
        return {
            "status": "ok",
            "plugins": len(plugins.list_names()) if plugins else 0,
            "hosters": registry.supported_hosters if registry else [],
        }

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
            return response
        finally:
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
