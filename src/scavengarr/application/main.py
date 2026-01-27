from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, cast

import structlog
from fastapi import FastAPI

from scavengarr.application.app_state import AppState
from scavengarr.infrastructure.config import AppConfig
from scavengarr.plugins import PluginRegistry

log = structlog.get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = cast(AppState, app.state)
    state.plugins = PluginRegistry(plugin_dir=state.config.plugin_dir)
    state.plugins.discover()
    yield
    log.info("app_shutdown")

def build_app(config: AppConfig) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state = AppState()
    app.state.config = config

    @app.get("/")
    async def root():
        return {"message": "Hello World"}

    return app
