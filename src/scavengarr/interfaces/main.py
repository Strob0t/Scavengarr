from __future__ import annotations

import time

import structlog
from fastapi import FastAPI, Request

from scavengarr.infrastructure.config import AppConfig
from scavengarr.interfaces.app_state import AppState
from scavengarr.interfaces.composition import lifespan

log = structlog.get_logger(__name__)


def build_app(config: AppConfig) -> FastAPI:
    """Build FastAPI app - NUR Konfiguration, KEINE Ressourcen-Initialisierung.

    Ressourcen (HTTP-Client, Cache, Plugins) werden in lifespan() erstellt.
    """
    app = FastAPI(
        title="Scavengarr",
        description="Prowlarr-compatible Torznab/Newznab indexer",
        version="0.1.0",
        lifespan=lifespan,  # ✅ Lifespan handles DI
    )

    # ✅ ONLY store Config in State
    app.state = AppState()
    app.state.config = config

    # ✅ Register routers (no dependencies needed)
    from scavengarr.interfaces.api.download.router import router as download_router
    from scavengarr.interfaces.api.torznab import router as torznab_router

    app.include_router(download_router)
    app.include_router(torznab_router, prefix="")

    # ✅ Health-Check (stateless)
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # ✅ Logging-Middleware (stateless)
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
