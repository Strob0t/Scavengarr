"""Plugin loading from Python files."""

from __future__ import annotations

import importlib.util
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

import structlog

from scavengarr.domain.plugins import (
    PluginLoadError,
    PluginProtocol,
)

log = structlog.get_logger(__name__)


def _import_module_from_path(path: Path) -> ModuleType:
    module_name = f"scavengarr_dynamic_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise PluginLoadError(f"Could not create import spec for {path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except SyntaxError as e:
        tb = traceback.format_exc()
        raise PluginLoadError(f"SyntaxError while importing {path}:\n{tb}") from e
    except Exception as e:
        tb = traceback.format_exc()
        raise PluginLoadError(f"Error while importing {path}:\n{tb}") from e

    return module


def load_python_plugin(path: Path) -> PluginProtocol:
    try:
        module = _import_module_from_path(path)
        if not hasattr(module, "plugin"):
            raise PluginLoadError("Plugin must export 'plugin' variable")

        plugin: Any = getattr(module, "plugin")
        if not hasattr(plugin, "search"):
            raise PluginLoadError("Plugin must have 'search' method")
        if (
            not hasattr(plugin, "name")
            or not isinstance(plugin.name, str)
            or not plugin.name
        ):
            raise PluginLoadError("Plugin must have non-empty 'name' attribute")

        return plugin
    except PluginLoadError as e:
        log.error(
            "plugin_load_failed",
            plugin_file=str(path),
            plugin_type="python",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        raise
