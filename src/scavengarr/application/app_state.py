from __future__ import annotations

from starlette.datastructures import State

from scavengarr.infrastructure.config import AppConfig
from scavengarr.plugins import PluginRegistry


class AppState(State):
    config: AppConfig
    plugins: PluginRegistry
